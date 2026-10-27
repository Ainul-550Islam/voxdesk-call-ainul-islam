import type { ReactElement } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";

/**
 * Minimal render harness.
 *
 * The console's pages are client components that fetch on mount, so a test has to let
 * both React's commit and the promise chain finish before asserting. Everything here is
 * the public React DOM API — no testing-library — because the pages must stay renderable
 * in the app's own runtime (React 18, jsdom) rather than only inside a helper.
 */

export interface Rendered {
  host: HTMLDivElement;
  text: () => string;
  find: (selector: string) => HTMLElement | null;
  findAll: (selector: string) => HTMLElement[];
  click: (element: HTMLElement) => Promise<void>;
  unmount: () => Promise<void>;
}

export async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

export async function render(element: ReactElement): Promise<Rendered> {
  (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(element);
  });
  await flush();
  await flush();

  return {
    host,
    text: () => host.textContent ?? "",
    find: (selector: string) => host.querySelector<HTMLElement>(selector),
    findAll: (selector: string) => Array.from(host.querySelectorAll<HTMLElement>(selector)),
    click: async (target: HTMLElement) => {
      await act(async () => {
        target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await flush();
    },
    unmount: async () => {
      await act(async () => {
        root.unmount();
      });
      host.remove();
    },
  };
}

/** Find a button by its accessible text. */
export function buttonByText(view: Rendered, label: string): HTMLElement {
  const match = view
    .findAll("button")
    .find((button) => (button.textContent ?? "").trim().toLowerCase() === label.toLowerCase());
  if (!match) {
    const seen = view
      .findAll("button")
      .map((button) => (button.textContent ?? "").trim())
      .join(" | ");
    throw new Error(`button "${label}" not found. Buttons present: ${seen}`);
  }
  return match;
}

/** Type into an input identified by its label text. */
export async function typeInto(view: Rendered, label: string, value: string): Promise<void> {
  const field = view.findAll("label").find((element) =>
    (element.textContent ?? "").toLowerCase().includes(label.toLowerCase()),
  );
  const input = field?.querySelector("input, select, textarea");
  if (!input) throw new Error(`no input labelled "${label}"`);
  await act(async () => {
    const descriptor = Object.getOwnPropertyDescriptor(
      input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype,
      "value",
    );
    descriptor?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}
