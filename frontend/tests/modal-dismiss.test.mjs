import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { useDialogDismiss } from "../src/components/shared/useDialogDismiss.ts";

function harness(canDismiss = true) {
  const dialog = { open: true, getBoundingClientRect: () => ({ left: 40, right: 360, top: 50, bottom: 550 }) };
  let handlers, dismissals = 0, prevented = 0, stopped = 0;
  function Harness() {
    handlers = useDialogDismiss(() => dismissals++, canDismiss);
    return null;
  }
  renderToStaticMarkup(createElement(Harness));
  function event(overrides = {}) {
    return {
      target: dialog, currentTarget: dialog, button: 0, isPrimary: true, pointerId: 3,
      clientX: 10, clientY: 100,
      preventDefault: () => prevented++, stopPropagation: () => stopped++, ...overrides,
    };
  }
  return { handlers, dialog, event, calls: () => ({ dismissals, prevented, stopped }) };
}

test("a primary backdrop click dismisses once and consumes the click", () => {
  const { handlers, event, calls } = harness();
  handlers.onPointerDown(event());
  handlers.onPointerUp(event());
  assert.equal(calls().dismissals, 0);
  handlers.onClick(event());
  handlers.onClick(event());
  assert.deepEqual(calls(), { dismissals: 1, prevented: 1, stopped: 1 });
});

test("interior clicks, boundary-crossing drags and cancelled pointers keep the dialog open", () => {
  const { handlers, event, calls } = harness();
  for (const [down, up] of [
    [{ clientX: 80 }, { clientX: 80 }],
    [{ clientX: 80 }, {}],
    [{}, { clientX: 80 }],
    [{ button: 2 }, {}],
    [{ isPrimary: false }, {}],
    [{}, { pointerId: 9 }],
    [{ target: {} }, {}],
  ]) {
    handlers.onPointerDown(event(down));
    handlers.onPointerUp(event(up));
    handlers.onClick(event(up));
  }
  handlers.onPointerDown(event());
  handlers.onPointerCancel();
  handlers.onPointerUp(event());
  handlers.onClick(event());
  assert.deepEqual(calls(), { dismissals: 0, prevented: 0, stopped: 0 });
});

test("Escape uses the same dismissal policy and a child dialog's cancel is ignored", () => {
  const available = harness();
  available.handlers.onCancel(available.event({ target: {} }));
  available.handlers.onCancel(available.event());
  assert.deepEqual(available.calls(), { dismissals: 1, prevented: 1, stopped: 1 });

  const busy = harness(false);
  busy.handlers.onPointerDown(busy.event());
  busy.handlers.onPointerUp(busy.event());
  busy.handlers.onClick(busy.event());
  busy.handlers.onCancel(busy.event());
  assert.deepEqual(busy.calls(), { dismissals: 0, prevented: 1, stopped: 1 });
});
