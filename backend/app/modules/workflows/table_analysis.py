"""Analyze authorized attachments with bounded, reproducible table operations."""

from collections import defaultdict
from collections.abc import Iterable
import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, localcontext
from html import escape
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
from typing import Annotated, Literal
from uuid import UUID
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from defusedxml.common import DefusedXmlException
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.auth.models import User
from app.modules.chat.independent import RequestCancelled
from app.modules.knowledge import providers, service as knowledge
from app.modules.knowledge.extract import archive
from . import artifacts
from .conversations import owned
from .models import Artifact


TOOL_NAME = 'analyze_table'
FORMATS = {'.csv', '.tsv', '.xlsx'}
MAX_ROWS = 50_000
MAX_COLUMNS = 100
MAX_CELLS = 1_000_000
MAX_GROUPS = 500
MAX_CHART_GROUPS = 40
MAX_CATALOG = 12
CELL_LENGTH = 4000
NUMBER = re.compile(r'[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d*)?|\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?', re.ASCII)
Column = Annotated[str, Field(min_length=1, max_length=166)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Source(Input):
    document_id: UUID
    sheet: str | None = Field(None, min_length=1, max_length=31)


class Join(Input):
    right: Source
    left_on: Column
    right_on: Column
    kind: Literal['inner', 'left'] = 'inner'


class Metric(Input):
    operation: Literal['count', 'sum', 'average', 'min', 'max']
    column: Column | None = None
    label: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def column_required(self):
        if self.operation != 'count' and self.column is None:
            raise ValueError('집계할 열을 지정하세요.')
        return self


class Analysis(Input):
    action: Literal['inspect', 'aggregate']
    source: Source
    join: Join | None = None
    append: Source | None = None
    group_by: list[Column] = Field(default_factory=list, max_length=4)
    month_of: Column | None = None
    metrics: list[Metric] = Field(default_factory=list, max_length=6)
    output: Literal['chat', 'xlsx', 'chart', 'both'] = 'chat'
    title: str = Field('표 분석 결과', min_length=1, max_length=100)
    chart_metric: Column | None = None

    @model_validator(mode='after')
    def valid_operation(self):
        if self.join and self.append:
            raise ValueError('열 결합과 행 이어붙이기 중 하나를 선택하세요.')
        if self.action == 'aggregate' and not self.metrics:
            raise ValueError('집계 항목을 하나 이상 선택하세요.')
        if self.action == 'inspect' and (self.join or self.append or self.metrics or self.group_by or self.month_of
                                        or self.output != 'chat' or self.chart_metric):
            raise ValueError('열 확인 요청에는 한 파일과 시트만 지정하세요.')
        if self.chart_metric and self.output not in {'chart', 'both'}:
            raise ValueError('차트 집계 열을 사용하려면 차트 출력을 선택하세요.')
        return self


def tool():
    schema = Analysis.model_json_schema()
    for definition in [schema, *schema['$defs'].values()]:
        definition.pop('title', None)
        for prop in definition.get('properties', {}).values():
            prop.pop('title', None)
    return {'type': 'function', 'function': {'name': TOOL_NAME, 'parameters': schema, 'description': (
        'Inspect authorized CSV/TSV/XLSX files, or calculate over all data rows. Inspect for exact headers and sheet names first. '
        'Join exact keys with a unique right key (right columns are prefixed right.), or append rows from identical headers. '
        'Use month_of to group a date column by calendar month. count without a column counts rows; with a column it counts nonblank values. '
        'Use chart for an HTML bar chart and both for an XLSX and chart. Cite calculated values and row coverage.')}}


def requested(question):
    return bool(re.search(
        r'표\s*분석|데이터\s*분석|스프레드시트|엑셀|\.xlsx\b|\.csv\b|\.tsv\b|열\s*이름|시트|'
        r'집계|합계|합산|평균|최솟값|최댓값|조인|중복|결측|월별|연도별|차트|그래프|계산|'
        r'\b(?:spreadsheet|xlsx|csv|tsv|aggregat\w*|join|append|pivot|sum|average|count|chart|group by|columns|sheets)\b',
        question, re.I,
    ))


def catalog(bind, user_id, conversation_id, options):
    scope = {key: options.get(key, []) for key in ('document_ids', 'base_ids')}
    if not get_settings().knowledge_enabled or not (any(scope.values()) or options.get('_auto_scope')):
        return []
    if not any(scope.values()):
        scope['_auto_scope'] = True
    with Session(bind) as db:
        _authorized(db, user_id, conversation_id)
        explicit = {str(identifier): position for position, identifier in enumerate(scope['document_ids'])}
        tables = [doc for doc in knowledge.selected_documents(db, user_id, scope, conversation_id)
                  if Path(doc.filename).suffix.lower() in FORMATS]
        tables.sort(key=lambda doc: (explicit.get(doc.id, len(explicit)), -doc.created_at, doc.id))
        return [{'document_id': doc.id, 'filename': doc.filename} for doc in tables[:MAX_CATALOG]]


def evidence(result):
    if 'columns' not in result:
        return '시트 목록: ' + ', '.join(result.get('sheets', []))
    headers = result['columns']
    row_count = result.get('coverage', {}).get('processed_rows', result.get('total_rows', 0))
    lines = [f'처리한 데이터 행: {row_count:,}', '열: ' + ' | '.join(headers)]
    lines.extend(' | '.join(str(cell) if cell is not None else '빈 값' for cell in row) for row in result['rows'])
    combine = result.get('coverage', {}).get('combine', {})
    if combine.get('kind') in {'inner', 'left'}:
        lines.append(f'결합 키: {combine["left_on"]} / {combine["right_on"]}. '
            f'왼쪽 일치 {combine["matched_left_rows"]:,}행, 불일치 {combine["unmatched_left_rows"]:,}행, '
            f'오른쪽 불일치 {combine["unmatched_right_rows"]:,}행.')
    for source in result['documents']:
        missing = source.get('uncomputed_formula_cells', {})
        if missing:
            lines.append(f'{source["filename"]} · {source["sheet"]}: 저장된 값이 없는 수식 {sum(missing.values()):,}개를 빈 값으로 처리했습니다.')
    if result.get('preview_truncated'):
        lines.append(f'전체 {result["total_result_rows"]:,}개 결과 중 일부를 표시합니다.')
    return '\n'.join(lines)


def _authorized(db, user_id, conversation_id):
    user = db.get(User, user_id)
    if not user or not user.is_active or user.registration_status != 'approved':
        raise HTTPException(403, '표 분석 권한이 변경되었습니다.')
    owned(db, user_id, conversation_id)


def verify(bind, user_id, conversation_id, documents):
    with Session(bind) as db:
        _authorized(db, user_id, conversation_id)
        for source in documents:
            doc = knowledge.document_access(db, user_id, source['document_id'], conversation_id=conversation_id)
            if (not get_settings().knowledge_enabled or doc.status != 'ready' or doc.index_profile != providers.profile()
                    or doc.sha256 != source['sha256'] or doc.revision != source['revision']):
                raise HTTPException(409, '분석 중 첨부 파일이나 색인 버전이 변경되었습니다. 다시 요청하세요.')


@dataclass
class Table:
    columns: list[str]
    rows: list[tuple[str | None, ...]]
    blank_rows: int = 0
    uncomputed: dict[str, int] = field(default_factory=dict)

    def column_index(self, name):
        try:
            return self.columns.index(name)
        except ValueError:
            raise HTTPException(422, f'표에 {name!r} 열이 없습니다. 시트와 열 이름을 먼저 확인하세요.') from None


def _check(cancelled):
    if cancelled and cancelled():
        raise RequestCancelled()


def _cell(value):
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > CELL_LENGTH or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text):
        raise HTTPException(422, '표에 길이 제한을 넘거나 처리할 수 없는 문자가 있는 셀이 있습니다.')
    return text or None


def _excel_value(cached, cell):
    if cell.data_type == 'f':
        return cached.value
    return cell.value


def _excel_records(cached, formulas):
    # Separate data-only and formula readers distinguish an empty cell from an
    # unevaluated formula without executing or interpreting spreadsheet code.
    for cached_row, formula_row in zip(cached.iter_rows(), formulas.iter_rows(), strict=True):
        cells = tuple(zip(cached_row, formula_row, strict=True))
        flags = tuple(cell.data_type == 'f' and saved.value is None for saved, cell in cells)
        yield tuple(_excel_value(saved, cell) for saved, cell in cells), flags


def _parse_rows(records: Iterable, cancelled, *, spreadsheet=False):
    columns = []
    rows = []
    blank_rows = 0
    uncomputed = defaultdict(int)
    for index, item in enumerate(records, 1):
        record, flags = item if spreadsheet else (item, ())
        if index % 256 == 0:
            _check(cancelled)
        if index > MAX_ROWS + 1 or index * len(columns) > MAX_CELLS:
            raise HTTPException(422, f'표는 헤더를 포함해 최대 {MAX_ROWS + 1:,}행과 100만 셀까지 분석할 수 있습니다.')
        if len(record) > MAX_COLUMNS and (not spreadsheet or any(flags[MAX_COLUMNS:])
                                         or any(value is not None for value in record[MAX_COLUMNS:])):
            raise HTTPException(422, '한 시트에서 열은 최대 100개까지 분석할 수 있습니다.')
        values = tuple(map(_cell, record[:MAX_COLUMNS]))
        if not any(value is not None for value in values) and not any(flags):
            blank_rows += 1
            continue
        if not columns:
            if any(flags):
                raise HTTPException(422, '표의 헤더에 계산되지 않은 수식이 있습니다.')
            width = max(i for i, value in enumerate(values, 1) if value is not None)
            columns = list(values[:width])
            if None in columns or len(set(columns)) != len(columns) or any(len(name) > 160 for name in columns):
                raise HTTPException(422, '첫 데이터 행에는 빈칸이나 중복 없이 열 이름을 지정하세요.')
            continue
        width = len(columns)
        if any(value is not None for value in values[width:]) or any(flags[width:]):
            raise HTTPException(422, f'{index}행의 데이터가 헤더 열 범위를 초과합니다.')
        for column, missing in zip(columns, flags):
            if missing:
                uncomputed[column] += 1
        rows.append(values[:width] + (None,) * max(0, width - len(values)))
    if not columns:
        raise HTTPException(422, '표에서 헤더를 찾지 못했습니다.')
    return Table(columns, rows, blank_rows, dict(uncomputed))


def _load(bind, user_id, conversation_id, source, permitted, action, cancelled):
    document_id = str(source.document_id)
    if document_id not in permitted:
        raise HTTPException(404, '현재 대화에서 선택한 표를 찾을 수 없습니다.')
    with Session(bind) as db:
        _authorized(db, user_id, conversation_id)
        doc = knowledge.document_access(db, user_id, document_id, conversation_id=conversation_id)
        if doc.status != 'ready' or doc.index_profile != providers.profile():
            raise HTTPException(409, '표의 색인이 준비될 때까지 기다려 주세요.')
        raw = doc.raw
        details = {'document_id': doc.id, 'filename': doc.filename, 'sha256': doc.sha256, 'revision': doc.revision}
    suffix = Path(details['filename']).suffix.lower()
    _check(cancelled)
    try:
        if suffix in {'.csv', '.tsv'}:
            if source.sheet:
                raise HTTPException(422, 'CSV·TSV에는 시트가 없습니다.')
            delimiter = ',' if suffix == '.csv' else '\t'
            records = csv.reader(StringIO(raw.decode('utf-8-sig'), newline=''), delimiter=delimiter, strict=True)
            return _parse_rows(records, cancelled), details, []
        from openpyxl import load_workbook
        archive(raw)
        workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True, keep_links=False)
        try:
            sheets = [sheet.title for sheet in workbook.worksheets]
            if not sheets:
                raise HTTPException(422, '분석할 워크시트가 없습니다.')
            if source.sheet is None and len(sheets) != 1:
                if action == 'inspect':
                    return None, details, sheets
                raise HTTPException(422, '시트가 여러 개입니다. 시트 이름을 먼저 확인하고 하나를 지정하세요.')
            sheet_name = source.sheet or sheets[0]
            if sheet_name not in sheets:
                raise HTTPException(422, '파일에 지정한 시트가 없습니다.')
            sheet = workbook[sheet_name]
            sheet.reset_dimensions()  # Measure actual cells instead of trusting the file's declared bounds.
            formulas = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            try:
                formula_sheet = formulas[sheet_name]
                formula_sheet.reset_dimensions()
                table = _parse_rows(_excel_records(sheet, formula_sheet), cancelled, spreadsheet=True)
            finally:
                formulas.close()
            details.update(sheet=sheet_name, formula_policy='cached_values_only', uncomputed_formula_cells=table.uncomputed)
            return table, details, sheets
        finally:
            workbook.close()
    except (csv.Error, BadZipFile, ValueError, KeyError, TypeError, IndexError, OSError, OverflowError, ParseError, DefusedXmlException):
        raise HTTPException(422, '표를 읽지 못했습니다. CSV·TSV는 UTF-8로 저장하고 XLSX 파일이 정상인지 확인하세요.') from None
    except ImportError:
        raise HTTPException(503, 'XLSX 분석 의존성을 설치하세요.') from None


def _combine(left, right, spec, cancelled):
    if spec is None:
        if set(left.columns) != set(right.columns):
            raise HTTPException(422, '행을 이어붙일 두 표의 열 이름이 일치해야 합니다.')
        order = [right.column_index(column) for column in left.columns]
        rows = left.rows.copy()
        for index, row in enumerate(right.rows):
            if index % 512 == 0:
                _check(cancelled)
            rows.append(tuple(row[i] for i in order))
        return Table(left.columns, rows), {'kind': 'append', 'rows': len(rows)}

    left_key, right_key = left.column_index(spec.left_on), right.column_index(spec.right_on)
    by_key = {}
    for index, row in enumerate(right.rows):
        if index % 512 == 0:
            _check(cancelled)
        key = row[right_key]
        if key is None:
            continue
        if key in by_key:
            raise HTTPException(422, '오른쪽 표의 결합 열에 중복 키가 있습니다. 고유한 결합 키를 지정하세요.')
        by_key[key] = row
    right_columns = ['right.' + name for name in right.columns]
    if set(right_columns) & set(left.columns):
        raise HTTPException(422, '두 표를 결합하면 열 이름이 겹칩니다. 원본 열 이름을 변경해 주세요.')
    rows, used = [], set()
    matched = 0
    empty_right = (None,) * len(right.columns)
    for index, row in enumerate(left.rows):
        if index % 512 == 0:
            _check(cancelled)
        key = row[left_key]
        other = by_key.get(key)
        if other is not None:
            matched += 1
            used.add(key)
            rows.append(row + other)
        elif spec.kind == 'left':
            rows.append(row + empty_right)
    return Table(left.columns + right_columns, rows), {
        'kind': spec.kind, 'rows': len(rows), 'matched_left_rows': matched,
        'unmatched_left_rows': len(left.rows) - matched, 'unmatched_right_rows': len(right.rows) - len(used),
        'left_on': spec.left_on, 'right_on': spec.right_on,
    }


def _month(value, column):
    if value is None:
        return None
    try:
        if match := re.fullmatch(r'(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?', value):
            return date(int(match[1]), int(match[2]), int(match[3] or 1)).isoformat()[:7]
        return datetime.fromisoformat(value).date().isoformat()[:7]
    except ValueError:
        raise HTTPException(422, f'{column!r} 열에 날짜로 읽을 수 없는 값이 있습니다. YYYY-MM-DD 형식을 확인하세요.') from None


def _number(value, column):
    if len(value) <= 80 and NUMBER.fullmatch(value):
        number = Decimal(value.replace(',', ''))
        if not number:
            return Decimal(0)
        if len(number.as_tuple().digits) <= 30 and -18 <= number.adjusted() <= 30:
            return number
    raise HTTPException(422, f'{column!r} 열에 유효하지 않거나 범위를 벗어난 숫자가 있습니다. 문자·단위·엑셀 오류를 확인하세요.')


@dataclass
class Accumulator:
    valid: int = 0
    total: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def add(self, metric, value):
        if value is None:
            return
        self.valid += 1
        if metric.operation == 'count':
            return
        number = _number(value, metric.column)
        self.total += number
        self.minimum = number if self.minimum is None else min(self.minimum, number)
        self.maximum = number if self.maximum is None else max(self.maximum, number)

    def value(self, operation):
        if operation == 'count':
            return self.valid
        if self.valid == 0:
            return None
        if operation == 'average':
            with localcontext() as context:
                context.prec = 28
                return self.total / self.valid
        return {'sum': self.total, 'min': self.minimum, 'max': self.maximum}[operation]


def _aggregate(table, data, cancelled):
    groups = [table.column_index(name) for name in data.group_by]
    month_index = table.column_index(data.month_of) if data.month_of else None
    group_names = data.group_by + ([data.month_of + ' (월)'] if month_index is not None else [])
    metric_names = [metric.label or (metric.operation + (f'({metric.column})' if metric.column else '')) for metric in data.metrics]
    columns = group_names + metric_names
    if len(columns) != len(set(columns)):
        raise HTTPException(422, '그룹·집계 열의 출력 이름은 서로 달라야 합니다.')
    indexes = [table.column_index(metric.column) if metric.column else None for metric in data.metrics]
    buckets = defaultdict(lambda: [Accumulator() for _ in data.metrics])
    if not group_names:
        buckets[()] = [Accumulator() for _ in data.metrics]  # An empty table still has a total of zero rows.
    with localcontext() as context:
        context.prec = 100
        for index, row in enumerate(table.rows):
            if index % 512 == 0:
                _check(cancelled)
            key = tuple(row[i] for i in groups)
            if month_index is not None:
                key += (_month(row[month_index], data.month_of),)
            if key not in buckets and len(buckets) >= MAX_GROUPS:
                raise HTTPException(422, '결과가 500개 그룹을 초과합니다. 집계 단위를 줄이세요.')
            for accumulator, metric, column in zip(buckets[key], data.metrics, indexes):
                accumulator.add(metric, row[column] if column is not None else '1')
        ordered = sorted(buckets, key=lambda key: tuple((value is None, value or '') for value in key))
        rows = [(*key, *(value.value(metric.operation) for value, metric in zip(buckets[key], data.metrics))) for key in ordered]
    valid = [sum(bucket[index].valid for bucket in buckets.values()) for index in range(len(data.metrics))]
    return columns, rows, {'processed_rows': len(table.rows), 'metric_values': [
        {'label': label, 'operation': metric.operation, 'column': metric.column,
         'used_rows': used, 'missing_rows': len(table.rows) - used}
        for label, used, metric in zip(metric_names, valid, data.metrics)], 'average_significant_digits': 28}, len(group_names)


def _exact(value):
    if not isinstance(value, Decimal):
        return value
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _excel(value):
    if isinstance(value, Decimal):
        # Excel retains up to 15 significant digits. Preserve any higher-precision result as text.
        floating = float(value)
        if Decimal(format(floating, '.15g')) == value:
            return floating
    return _exact(value)


def _preview(rows, max_rows=20, max_bytes=2600):
    visible = []
    for row in rows[:max_rows]:
        candidate = [_exact(value) for value in row]
        max_bytes -= len(json.dumps(candidate, ensure_ascii=False).encode())
        if max_bytes < 0:
            break
        visible.append(candidate)
    return visible


def _chart(title, columns, rows, group_count, metric):
    index = columns.index(metric) if metric in columns else group_count
    if index < group_count or metric and metric not in columns:
        raise HTTPException(422, '차트에 사용할 집계 열을 확인하세요.')
    visible = rows[:MAX_CHART_GROUPS]
    values = [Decimal(row[index]) for row in visible if row[index] is not None]
    low, high = min([Decimal(0), *values]), max([Decimal(0), *values])
    scale = high - low or Decimal(1)
    bars = []
    for row in visible:
        label = ' / '.join(value or '빈 값' for value in row[:group_count]) or '전체'
        if len(label) > 120:
            label = label[:119] + '…'
        value = row[index]
        number = Decimal(value) if value is not None else Decimal(0)
        offset = 100 * (min(number, Decimal(0)) - low) / scale
        width = 100 * abs(number) / scale
        baseline = -100 * low / scale
        bars.append(f'<li><span>{escape(label)}</span><span class="track" aria-hidden="true">'
                    f'<i class="axis" style="left:{baseline:.3f}%"></i>'
                    f'<i class="bar" style="left:{offset:.3f}%;width:{width:.3f}%"></i></span>'
                    f'<strong>{escape(str(_exact(value))) if value is not None else "빈 값"}</strong></li>')
    heading = escape(title)
    caption = f'{escape(columns[index])} · {len(bars)}개 표시 / 전체 {len(rows)}개 그룹'
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{heading}</title><style>'
        'body{font:1rem/1.5 system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:56rem;color:#17202c;background:#fff}'
        'figure{margin:0}figcaption{color:#46546a}ul{list-style:none;padding:0}li{display:grid;'
        'grid-template-columns:minmax(4rem,1fr) minmax(3rem,2fr) minmax(2rem,1fr);gap:.65rem;align-items:center;margin:.7rem 0}'
        'span,strong{overflow-wrap:anywhere}.track{position:relative;height:1rem;background:#e8edf3}'
        '.bar,.axis{position:absolute;height:100%}.bar{background:#2154a5}.axis{border-left:1px solid #53637b;z-index:1}'
        '</style></head><body>'
        f'<h1>{heading}</h1><figure><figcaption>{caption}</figcaption><ul>{"".join(bars)}</ul></figure></body></html>'
    ).encode()


def _outputs(bind, user_id, turn, data, columns, rows, group_count, analysis, cancelled):
    prepared = []
    if data.output in {'xlsx', 'both'}:
        try:
            document = artifacts.DocumentInput(name=data.title, format='xlsx', columns=columns,
                rows=[[_excel(value) for value in row] for row in rows], content='' if rows else '조건에 맞는 행이 없습니다.')
            payload = document.model_dump() | {'analysis': analysis}
            prepared.append({'name': artifacts.file_name(data.title, 'xlsx'), 'file_format': 'xlsx',
                             'raw': artifacts.build(document), 'payload_json': json.dumps(payload, ensure_ascii=False)})
        except ImportError:
            raise HTTPException(503, 'XLSX 생성 의존성을 설치하세요.') from None
        except (ValueError, KeyError):
            raise HTTPException(422, '집계 결과를 XLSX로 저장하지 못했습니다. 집계 표의 크기나 셀 형식을 확인하세요.') from None
    if data.output in {'chart', 'both'}:
        if not rows:
            raise HTTPException(422, '차트로 표시할 집계 결과가 없습니다.')
        raw = _chart(data.title, columns, rows, group_count, data.chart_metric)
        payload = artifacts.DocumentInput(name=data.title, format='html', content=raw.decode()).model_dump()
        payload['analysis'] = analysis
        prepared.append({'name': artifacts.file_name(data.title, 'html'), 'file_format': 'html', 'raw': raw,
                         'payload_json': json.dumps(payload, ensure_ascii=False)})
    files = []
    try:
        for output in prepared:
            _check(cancelled)
            files.append(artifacts.store(bind, user_id, turn.assistant_id, turn.request_id, **output))
    except Exception:
        if files:
            with Session(bind) as db:
                db.execute(delete(Artifact).where(Artifact.owner_id == user_id, Artifact.run_id == turn.request_id,
                    Artifact.status == 'draft', Artifact.id.in_([file['id'] for file in files])))
                db.commit()
        raise
    return files


def execute(bind, user_id, turn, options, args, *, cancelled=None):
    try:
        data = Analysis.model_validate(args)
    except ValidationError as error:
        issue = error.errors(include_input=False, include_url=False)[0]
        field = '.'.join(str(part) for part in issue['loc'])
        raise HTTPException(422, f'표 분석 입력을 확인하세요. {field} {issue["msg"]}') from None

    permitted = {entry['document_id'] for entry in catalog(bind, user_id, turn.conversation_id, options)}
    if '_tables' in options:
        permitted.intersection_update(entry['document_id'] for entry in options['_tables'])
    left, original, sheets = _load(bind, user_id, turn.conversation_id, data.source, permitted, data.action, cancelled)
    documents = [original]
    verify(bind, user_id, turn.conversation_id, documents)
    if data.action == 'inspect':
        return ({'title': '표 구조 확인', 'documents': documents, 'sheets': sheets, 'sheet_required': left is None}
                | ({'columns': left.columns, 'rows': _preview(left.rows, max_rows=5), 'total_rows': len(left.rows),
                    'blank_rows': left.blank_rows} if left is not None else {})), []

    coverage = {'inputs': [{'rows': len(left.rows), 'blank_rows': left.blank_rows}]}
    table = left
    if additional := (data.join.right if data.join else data.append):
        right, original, _ = _load(bind, user_id, turn.conversation_id, additional, permitted, data.action, cancelled)
        documents.append(original)
        coverage['inputs'].append({'rows': len(right.rows), 'blank_rows': right.blank_rows})
        table, coverage['combine'] = _combine(left, right, data.join, cancelled)

    columns, rows, metrics, group_count = _aggregate(table, data, cancelled)
    coverage.update(metrics)
    preview = _preview(rows)
    result = {'title': data.title, 'documents': documents, 'columns': columns, 'rows': preview, 'total_result_rows': len(rows),
              'preview_truncated': len(preview) < len(rows), 'coverage': coverage,
              'group_by': data.group_by, 'month_of': data.month_of}
    if data.output in {'chart', 'both'}:
        result['chart_display_rows'] = min(MAX_CHART_GROUPS, len(rows))
        result['chart_metric'] = data.chart_metric or columns[group_count]
    _check(cancelled)
    verify(bind, user_id, turn.conversation_id, documents)
    files = _outputs(bind, user_id, turn, data, columns, rows, group_count, result, cancelled)
    return result | {'files': files}, files
