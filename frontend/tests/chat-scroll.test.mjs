import assert from 'node:assert/strict';
import { test } from 'node:test';
import { scrollFollowState, scrollPosition } from '../src/lib/chat-scroll.ts';

const position = (scrollTop, scrollHeight = 1000, clientHeight = 400) => ({ scrollTop, scrollHeight, clientHeight });

test('새 청크로 높이가 늘어나도 하단을 추적하던 상태는 유지한다', () => {
  const before = position(600);
  const grown = position(600, 1200);
  assert.deepEqual(scrollFollowState(before, grown, true, false), { direction: null, following: true });
  assert.deepEqual(scrollFollowState(grown, position(800, 1200), true, false), { direction: 'newer', following: true });
});

test('1px보다 작은 상향 이동도 추적을 중단하고 다음 청크가 도착해도 재개하지 않는다', () => {
  const before = position(600);
  for (const distance of [0.5, 1, 3, 24, 99]) {
    const reading = position(600 - distance);
    const paused = scrollFollowState(before, reading, true, false);
    assert.deepEqual(paused, { direction: 'older', following: false });
    assert.equal(scrollFollowState(reading, position(reading.scrollTop, 1300), paused.following, false).following, false);
  }
});

test('하단에 가까이 접근할 때는 멈춤을 유지하고 실제 하단 복귀 후 다시 추적한다', () => {
  const reading = position(300);
  const near = position(580);
  const paused = scrollFollowState(reading, near, false, false);
  assert.equal(paused.following, false);
  const resumed = scrollFollowState(near, position(600), paused.following, false);
  assert.equal(resumed.following, true);
  assert.equal(scrollFollowState(position(600), position(600, 1300), resumed.following, false).following, true);
});

test('scroll 이벤트보다 먼저 받은 상향 입력으로 추적이 해제되면 새 렌더가 하단을 강제하지 않는다', () => {
  const before = position(600);
  assert.equal(scrollFollowState(before, before, false, false).following, false);
  assert.equal(scrollFollowState(before, position(600, 1200), false, false).following, false);
});

test('창 확대 또는 콘텐츠 축소로 하단에 보정된 이동은 추적 중단이나 이전 페이지 로드가 아니다', () => {
  const before = position(600);
  for (const clamped of [position(500, 1000, 500), position(450, 850, 400)]) {
    assert.deepEqual(scrollFollowState(before, clamped, true, false), { direction: null, following: true });
  }
  assert.deepEqual(scrollFollowState(before, position(450, 1000, 500), true, false), { direction: 'older', following: false });
});

test('이전 이력 페이지의 하단은 최신 답변 하단으로 취급하지 않는다', () => {
  assert.deepEqual(scrollFollowState(position(300), position(600), false, true), { direction: 'newer', following: false });
  assert.equal(scrollFollowState(position(600), position(600), true, true).following, false);
});

test('스크롤 스냅샷은 이후 DOM 위치 변경과 독립적이다', () => {
  const element = position(600);
  const before = scrollPosition(element);
  element.scrollTop = 576;
  element.scrollHeight = 1200;
  assert.deepEqual(before, position(600));
  assert.equal(scrollFollowState(before, scrollPosition(element), true, false).following, false);
});
