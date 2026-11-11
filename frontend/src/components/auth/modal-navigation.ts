"use client";
import { createContext, useContext } from "react";

export const ModalNavigationContext = createContext<() => boolean>(() => true);

/** Keeps an async action's navigation scoped to the modal where it began. */
export function useModalNavigation() {
  return useContext(ModalNavigationContext);
}
