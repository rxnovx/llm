import { useRef, type MouseEvent, type PointerEvent, type SyntheticEvent } from "react";

function isBackdrop(event: MouseEvent<HTMLDialogElement>) {
  const dialog = event.currentTarget;
  if (!dialog.open || event.target !== dialog) return false;
  const bounds = dialog.getBoundingClientRect();
  return event.clientX < bounds.left || event.clientX > bounds.right
    || event.clientY < bounds.top || event.clientY > bounds.bottom;
}

export function useDialogDismiss(onDismiss: () => void, canDismiss = true) {
  const pressed = useRef<number | null>(null);
  const releasedOutside = useRef(false);
  const reset = () => {
    pressed.current = null;
    releasedOutside.current = false;
  };

  return {
    onCancel(event: SyntheticEvent<HTMLDialogElement>) {
      if (event.target !== event.currentTarget) return;
      event.preventDefault();
      event.stopPropagation();
      reset();
      if (canDismiss) onDismiss();
    },
    onPointerDown(event: PointerEvent<HTMLDialogElement>) {
      reset();
      if (canDismiss && event.isPrimary && event.button === 0 && isBackdrop(event)) {
        pressed.current = event.pointerId;
      }
    },
    onPointerUp(event: PointerEvent<HTMLDialogElement>) {
      releasedOutside.current = canDismiss && pressed.current === event.pointerId && isBackdrop(event);
      pressed.current = null;
    },
    onPointerCancel: reset,
    onClick(event: MouseEvent<HTMLDialogElement>) {
      const outsideClick = releasedOutside.current && isBackdrop(event);
      reset();
      if (!outsideClick) return;
      event.preventDefault();
      event.stopPropagation();
      if (canDismiss) onDismiss();
    },
  };
}
