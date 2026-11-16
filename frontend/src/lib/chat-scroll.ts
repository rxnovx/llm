export type ScrollPosition = { scrollTop: number; scrollHeight: number; clientHeight: number };
export type ScrollDirection = 'older' | 'newer' | null;

export function scrollPosition(element: ScrollPosition): ScrollPosition {
  return { scrollTop: element.scrollTop, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight };
}

export function scrollFollowState(previous: ScrollPosition, current: ScrollPosition, following: boolean, hasNewer: boolean) {
  const bottom = current.scrollHeight - current.scrollTop - current.clientHeight;
  // scrollTop은 소수점일 수 있으므로 반올림 오차만 허용합니다.
  const atBottom = bottom <= 1;
  let direction: ScrollDirection = current.scrollTop < previous.scrollTop ? 'older'
    : current.scrollTop > previous.scrollTop ? 'newer' : null;
  const rangeShrank = current.scrollHeight - current.clientHeight < previous.scrollHeight - previous.clientHeight;
  // 창이 커지거나 내용이 줄어 하단으로 보정된 이동은 사용자의 상향 스크롤이 아닙니다.
  const clampedToBottom = direction === 'older' && rangeShrank && atBottom;
  if (clampedToBottom) direction = null;
  // 이전 자동 이동의 늦은 scroll 이벤트가 상향 입력으로 중단한 추적을 되살리지 않게 합니다.
  const reachedBottom = atBottom && (direction === 'newer' || clampedToBottom);
  return { direction, following: !hasNewer && direction !== 'older' && (reachedBottom || following) };
}
