/// <reference lib="dom" />
/// <reference lib="dom.iterable" />

declare namespace JSX {
  type Element = any;
  type ElementClass = any;
  interface IntrinsicAttributes {
    key?: any;
  }
  interface IntrinsicElements {
    [elemName: string]: any;
  }
}

declare namespace React {
  type ReactNode = any;
  type ReactElement<P = any, T = any> = any;
  type ComponentType<P = {}> = FunctionComponent<P>;
  interface FunctionComponent<P = {}> {
    (props: P, context?: any): any;
    displayName?: string;
  }
  type FC<P = {}> = FunctionComponent<P>;
  type CSSProperties = Record<string, any>;
  type FormEvent<T = Element> = any;
  type ChangeEvent<T = Element> = any;
  type MouseEvent<T = Element> = any;
  type KeyboardEvent<T = Element> = any;
  type FocusEvent<T = Element> = any;
  type DragEvent<T = Element> = any;
  type RefObject<T> = { current: T | null };
  type MutableRefObject<T> = { current: T };
  type Dispatch<A> = (value: A) => void;
  type SetStateAction<S> = S | ((prevState: S) => S);

  interface HTMLAttributes<T = Element> {
    children?: any;
    className?: string;
    id?: string;
    role?: string;
    style?: CSSProperties;
    title?: string;
    tabIndex?: number;
    onClick?: (event: any) => any;
    onChange?: (event: any) => any;
    onKeyDown?: (event: any) => any;
    onSubmit?: (event: any) => any;
    [attr: string]: any;
  }

  interface ButtonHTMLAttributes<T = HTMLButtonElement> extends HTMLAttributes<T> {
    disabled?: boolean;
    type?: 'button' | 'submit' | 'reset' | string;
    name?: string;
    value?: string | number;
  }

  interface InputHTMLAttributes<T = HTMLInputElement> extends HTMLAttributes<T> {
    disabled?: boolean;
    type?: string;
    value?: any;
    defaultValue?: any;
    placeholder?: string;
    checked?: boolean;
    readOnly?: boolean;
    required?: boolean;
  }

  function useState<S>(initialState: S | (() => S)): [S, Dispatch<SetStateAction<S>>];
  function useState<S = undefined>(): [S | undefined, Dispatch<SetStateAction<S | undefined>>];
  function useEffect(effect: () => void | (() => void), deps?: readonly any[]): void;
  function useLayoutEffect(effect: () => void | (() => void), deps?: readonly any[]): void;
  function useMemo<T>(factory: () => T, deps: readonly any[]): T;
  function useCallback<T extends (...args: any[]) => any>(callback: T, deps: readonly any[]): T;
  function useRef<T>(initialValue: T): MutableRefObject<T>;
  function useRef<T>(initialValue: T | null): RefObject<T>;
  function useRef<T = undefined>(): MutableRefObject<T | undefined>;
  function useContext<T>(context: any): T;
  function useId(): string;
  function createContext<T>(defaultValue: T): any;
  function createElement(type: any, props?: any, ...children: any[]): any;
  function forwardRef<T, P = {}>(render: (props: P, ref: any) => any): any;
  function memo<T>(component: T): T;
  const Fragment: any;
  const StrictMode: any;
}

declare module 'react' {
  export = React;
}

declare module 'react/jsx-runtime' {
  export const Fragment: any;
  export function jsx(type: any, props: any, key?: any): any;
  export function jsxs(type: any, props: any, key?: any): any;
}

declare module 'react/jsx-dev-runtime' {
  export const Fragment: any;
  export function jsxDEV(type: any, props: any, key?: any, isStaticChildren?: boolean, source?: any, self?: any): any;
}

declare module 'react-dom' {
  export function createPortal(children: any, container: Element | DocumentFragment, key?: null | string): any;
  export function flushSync<R>(fn: () => R): R;
  const ReactDOMDefault: Record<string, any>;
  export default ReactDOMDefault;
}

declare module 'react-dom/client' {
  export interface Root {
    render(children: any): void;
    unmount(): void;
  }
  export function createRoot(container: Element | DocumentFragment, options?: any): Root;
  export function hydrateRoot(container: Element | Document, initialChildren: any, options?: any): Root;
}
