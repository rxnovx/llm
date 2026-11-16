import { expect, test, type Page } from '@playwright/test';
import type { ChatEvent } from '../src/types/chat';

type ControlledReply = { append: (content: string) => void; finish: () => void };
declare global { interface Window { scrollTestReply?: ControlledReply } }

async function startReply(page: Page, username: string) {
  await page.goto('/user/signup');
  await page.getByLabel('표시 이름', { exact: true }).fill('스크롤 사용자');
  await page.getByLabel('사용자명', { exact: true }).fill(username);
  await page.getByLabel('이메일', { exact: true }).fill(`${username}@example.com`);
  await page.getByLabel('비밀번호', { exact: true }).fill('ScrollBrowserPass123!');
  await page.getByLabel('비밀번호 확인', { exact: true }).fill('ScrollBrowserPass123!');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '회원가입', exact: true }).click();
  await expect(page.getByRole('button', { name: '모델 선택 · 테스트 모드', exact: true })).toBeEnabled();

  // 실제 대화 생성/전송 경로를 사용하되 각 SSE 청크의 도착 시점을 테스트가 제어합니다.
  await page.evaluate(() => {
    const originalFetch = window.fetch;
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      const requestUrl = args[0] instanceof Request ? args[0].url : String(args[0]);
      if (!/\/api\/chat\/conversations\/[^/?]+\/messages$/.test(requestUrl)
          || args[1]?.method !== 'POST' || !response.ok) return response;
      const frames = (await response.text()).split('\n\n').map(frame => {
        const name = /^event: (.+)$/m.exec(frame)?.[1];
        const data = /^data: (.+)$/m.exec(frame)?.[1];
        return name && data ? { event: name, data: JSON.parse(data) } as ChatEvent : null;
      });
      const start = frames.find(frame => frame?.event === 'start');
      const done = frames.find(frame => frame?.event === 'done');
      if (!start || start.event !== 'start' || !done || done.event !== 'done') throw new Error('테스트 응답이 완료되지 않았습니다.');
      let content = '';
      const encoder = new TextEncoder();
      const stream = new ReadableStream<Uint8Array>({ start(controller) {
        const emit = (event: ChatEvent) => controller.enqueue(encoder.encode(`event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`));
        emit(start);
        window.scrollTestReply = {
          append: value => { content += value; emit({ event: 'delta', data: { content: value } }); },
          finish: () => { emit({ ...done, data: { ...done.data, message: { ...done.data.message, content } } }); controller.close(); },
        };
      } });
      return new Response(stream, { status: response.status, headers: response.headers });
    };
  });
  await page.getByLabel('메시지', { exact: true }).fill('스크롤 중 읽을 수 있는 긴 답변을 주세요.');
  await page.getByRole('button', { name: '메시지 전송', exact: true }).click();
  await expect.poll(() => page.evaluate(() => Boolean(window.scrollTestReply))).toBe(true);
}

async function append(page: Page, label: string, lines = 5) {
  await page.evaluate(({ label, lines }) => window.scrollTestReply!.append(
    Array.from({ length: lines }, (_, index) => `\n\n${label} 문단 ${index + 1}: 스트리밍 중에도 사용자가 읽는 위치를 유지합니다.`).join(''),
  ), { label, lines });
  await expect(page.locator('.cw-markdown')).toContainText(`${label} 문단 ${lines}`);
}

const position = (page: Page) => page.locator('.cw-content').evaluate(element => ({
  top: element.scrollTop, bottom: element.scrollHeight - element.clientHeight - element.scrollTop,
}));
const expectBottom = (page: Page) => expect.poll(async () => (await position(page)).bottom).toBeLessThanOrEqual(1);
const settle = (page: Page) => page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
async function settleUserScroll(page: Page) {
  let previous = Number.NaN;
  let stable = 0;
  await expect.poll(async () => {
    const current = (await position(page)).top;
    stable = Math.abs(current - previous) <= 1 ? stable + 1 : 0;
    previous = current;
    return stable;
  }, { intervals: [50] }).toBeGreaterThanOrEqual(2);
}

test('조금 위로 스크롤하면 응답 추적을 멈추고 실제 하단 복귀와 최신 이동에서 재개한다', async ({ page }, info) => {
  await startReply(page, `scroll_${info.project.name}_${Date.now()}`);
  await append(page, '시작', 32);
  await expectBottom(page);
  await append(page, '계속 작성');
  await expectBottom(page);

  const viewport = page.locator('.cw-content');
  await viewport.hover();
  await page.mouse.wheel(0, -24);
  await expect.poll(async () => (await position(page)).bottom).toBeGreaterThan(10);
  await settleUserScroll(page);
  const stopped = await position(page);
  expect(stopped.bottom).toBeLessThan(100);
  await append(page, '위로 이동한 뒤');
  await settle(page);
  expect(Math.abs((await position(page)).top - stopped.top)).toBeLessThanOrEqual(1);
  await expect(page.getByRole('button', { name: '최신 메시지로 이동', exact: true })).toBeVisible();

  // 하단에 가까이 가기만 했을 때는 추적을 재개하지 않습니다.
  await viewport.evaluate(element => { element.scrollTop = element.scrollHeight - element.clientHeight - 20; });
  await settle(page);
  const nearBottom = await position(page);
  await append(page, '하단 근처');
  await settle(page);
  expect(Math.abs((await position(page)).top - nearBottom.top)).toBeLessThanOrEqual(1);

  await viewport.evaluate(element => { element.scrollTop = element.scrollHeight; });
  await expect(page.getByRole('button', { name: '최신 메시지로 이동', exact: true })).toHaveCount(0);
  await append(page, '직접 하단 복귀');
  await expectBottom(page);

  // 스크롤바 또는 스크롤 이벤트만으로도 3px 상향 이동을 감지합니다.
  await viewport.evaluate(element => { element.scrollTop -= 3; });
  await expect(page.getByRole('button', { name: '최신 메시지로 이동', exact: true })).toBeVisible();
  const tiny = await position(page);
  await append(page, '작은 이동');
  await settle(page);
  expect(Math.abs((await position(page)).top - tiny.top)).toBeLessThanOrEqual(1);

  await page.getByRole('button', { name: '최신 메시지로 이동', exact: true }).click();
  await expectBottom(page);
  await append(page, '버튼으로 복귀');
  await expectBottom(page);
  await page.evaluate(() => window.scrollTestReply!.finish());
  await expect(page.getByRole('button', { name: '답변 복사', exact: true })).toBeVisible();
});

test('상향 키보드·터치 의도와 창 크기 변경에서도 읽는 위치와 추적 상태를 유지한다', async ({ page }, info) => {
  await startReply(page, `scroll_input_${info.project.name}_${Date.now()}`);
  await append(page, '시작', 32);
  await expectBottom(page);
  const viewport = page.locator('.cw-content');
  await page.getByRole('article', { name: '모델 답변' }).evaluate(element => element.focus({ preventScroll: true }));
  await page.keyboard.press('ArrowUp');
  await expect.poll(async () => (await position(page)).bottom).toBeGreaterThan(10);
  await settleUserScroll(page);
  const keyboard = await position(page);
  await append(page, '키보드 상향');
  await settle(page);
  expect(Math.abs((await position(page)).top - keyboard.top)).toBeLessThanOrEqual(1);
  await page.getByRole('button', { name: '최신 메시지로 이동', exact: true }).click();
  await expectBottom(page);

  // 터치 의도가 scroll 이벤트보다 먼저 전달되면 새 토큰도 위치를 빼앗지 않습니다.
  await viewport.evaluate(element => {
    const touch = (clientY: number) => new Touch({ identifier: 1, target: element, clientX: 150, clientY });
    element.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(200)], bubbles: true }));
    element.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(220)], bubbles: true }));
  });
  await expect(page.getByRole('button', { name: '최신 메시지로 이동', exact: true })).toBeVisible();
  const touch = await position(page);
  await append(page, '터치 상향');
  await settle(page);
  expect(Math.abs((await position(page)).top - touch.top)).toBeLessThanOrEqual(1);

  const size = page.viewportSize()!;
  await page.setViewportSize({ ...size, height: size.height - 100 });
  await settle(page);
  expect(Math.abs((await position(page)).top - touch.top)).toBeLessThanOrEqual(1);
  await page.getByRole('button', { name: '최신 메시지로 이동', exact: true }).click();
  await expectBottom(page);
  await page.setViewportSize(size);
  await expectBottom(page);
  await append(page, '크기 변경 뒤');
  await expectBottom(page);
  await page.evaluate(() => window.scrollTestReply!.finish());
});
