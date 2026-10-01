// File: components/enterprise/TiltSurface.tsx — Pointer-aware 3D tilt surface with bounded rotation, keyboard-safe behavior and reduced-motion fallback, mobile-safe

"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  calculateBoundedTilt,
  createRafThrottle,
  prefersReducedMotion,
  getSafeDuration,
} from "@/lib/enterprise-ui/motion";

export interface TiltSurfaceProps extends React.HTMLAttributes<HTMLDivElement> {
  readonly maxRotationDeg?: number;
  readonly perspective?: number;
  readonly scaleOnHover?: number;
  readonly disabled?: boolean;
  readonly disableOnTouch?: boolean;
}

export function TiltSurface({
  maxRotationDeg = 8,
  perspective = 1000,
  scaleOnHover = 1.02,
  disabled = false,
  disableOnTouch = true,
  className,
  style,
  children,
  onPointerMove,
  onPointerLeave,
  onPointerEnter,
  ...rest
}: TiltSurfaceProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const [tilt, setTilt] = useState({ rotateX: 0, rotateY: 0 });
  const [isHovered, setIsHovered] = useState(false);
  const [isReducedMotion, setIsReducedMotion] = useState(false);
  const [isTouchDevice, setIsTouchDevice] = useState(false);

  useEffect(() => {
    setIsReducedMotion(prefersReducedMotion());
    setIsTouchDevice("ontouchstart" in window || navigator.maxTouchPoints > 0);

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handler = () => setIsReducedMotion(mq.matches);
    mq.addEventListener?.("change", handler);
    return () => mq.removeEventListener?.("change", handler);
  }, []);

  const shouldDisableTilt =
    disabled ||
    isReducedMotion ||
    (disableOnTouch && isTouchDevice);

  const throttledSetTilt = useCallback(
    createRafThrottle((x: unknown, y: unknown, rect: unknown) => {
      if (shouldDisableTilt) return;
      const r = rect as { left: number; top: number; width: number; height: number };
      const result = calculateBoundedTilt(
        x as number,
        y as number,
        r,
        maxRotationDeg
      );
      setTilt(result);
    }),
    [shouldDisableTilt, maxRotationDeg]
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      onPointerMove?.(e);
      if (shouldDisableTilt) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      throttledSetTilt(e.clientX, e.clientY, {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      });
    },
    [onPointerMove, shouldDisableTilt, throttledSetTilt]
  );

  const handlePointerEnter = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      onPointerEnter?.(e);
      setIsHovered(true);
    },
    [onPointerEnter]
  );

  const handlePointerLeave = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      onPointerLeave?.(e);
      setIsHovered(false);
      setTilt({ rotateX: 0, rotateY: 0 });
    },
    [onPointerLeave]
  );

  // Keyboard focus should NOT trigger tilt
  const handleFocus = useCallback(() => {
    // No tilt on keyboard focus — intentional
    setTilt({ rotateX: 0, rotateY: 0 });
  }, []);

  const transitionDuration = isReducedMotion ? 0 : getSafeDuration("normal");

  return (
    <div
      ref={containerRef}
      className={["enterprise-perspective", className].filter(Boolean).join(" ")}
      style={{
        perspective: `${perspective}px`,
        perspectiveOrigin: "center center",
        ...style,
      }}
      onPointerMove={handlePointerMove}
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
      onFocus={handleFocus}
      {...rest}
    >
      <div
        data-testid="tilt-surface-inner"
        data-tilt-x={tilt.rotateX}
        data-tilt-y={tilt.rotateY}
        data-hovered={isHovered ? "true" : "false"}
        data-reduced-motion={isReducedMotion ? "true" : "false"}
        style={{
          transformStyle: "preserve-3d",
          transform: shouldDisableTilt
            ? "none"
            : `rotateX(${tilt.rotateX}deg) rotateY(${tilt.rotateY}deg) scale3d(${isHovered ? scaleOnHover : 1}, ${isHovered ? scaleOnHover : 1}, 1)`,
          transition: shouldDisableTilt
            ? "none"
            : `transform ${transitionDuration}ms cubic-bezier(0.16, 1, 0.3, 1)`,
          willChange: shouldDisableTilt ? "auto" : "transform",
        }}
      >
        {children}
      </div>
    </div>
  );
}
