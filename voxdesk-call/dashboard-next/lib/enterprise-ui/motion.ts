// File: lib/enterprise-ui/motion.ts — Motion configuration helpers defining bounded animation timing and prefers-reduced-motion-safe defaults for enterprise UI

/**
 * VoxDesk Enterprise — Motion Helpers
 * Bounded durations, no perpetual high-frequency animation for ordinary content.
 * Respects prefers-reduced-motion.
 */

export type MotionDuration = "instant" | "fast" | "normal" | "slow" | "slower";

export type MotionEasing = "easeOut" | "easeInOut" | "spring" | "linear";

export type MotionConfig = {
  readonly duration: number; // ms
  readonly easing: string;
  readonly delay?: number;
};

export type HoverTransition = {
  readonly transform: string;
  readonly opacity: string;
  readonly filter: string;
};

export const motionDurations: Record<MotionDuration, number> = {
  instant: 0,
  fast: 120,
  normal: 200,
  slow: 320,
  slower: 480,
} as const;

export const motionEasings: Record<MotionEasing, string> = {
  easeOut: "cubic-bezier(0.16, 1, 0.3, 1)",
  easeInOut: "cubic-bezier(0.4, 0, 0.2, 1)",
  spring: "cubic-bezier(0.34, 1.56, 0.64, 1)",
  linear: "linear",
} as const;

export function getMotionConfig(
  duration: MotionDuration,
  easing: MotionEasing = "easeOut",
  delayMs = 0
): MotionConfig {
  return {
    duration: motionDurations[duration],
    easing: motionEasings[easing],
    delay: delayMs,
  };
}

export function getTransitionString(config: MotionConfig, properties: string[]): string {
  const d = `${config.duration}ms`;
  const e = config.easing;
  const delay = config.delay ? ` ${config.delay}ms` : "";
  return properties.map((p) => `${p} ${d} ${e}${delay}`).join(", ");
}

export const hoverTransitions: HoverTransition = {
  transform: `${motionDurations.normal}ms ${motionEasings.easeOut}`,
  opacity: `${motionDurations.fast}ms ${motionEasings.easeOut}`,
  filter: `${motionDurations.normal}ms ${motionEasings.easeOut}`,
};

export function getHoverTransition(): string {
  return `transform ${hoverTransitions.transform}, opacity ${hoverTransitions.opacity}, filter ${hoverTransitions.filter}, box-shadow ${motionDurations.normal}ms ${motionEasings.easeOut}, border-color ${motionDurations.normal}ms ${motionEasings.easeOut}`;
}

export function getEntranceTransition(index = 0): string {
  const delay = index * 40; // bounded stagger, max 40ms per item
  const config = getMotionConfig("normal", "easeOut", delay);
  return getTransitionString(config, ["opacity", "transform", "filter"]);
}

export function getPanelElevationTransition(): string {
  const config = getMotionConfig("normal", "easeOut");
  return getTransitionString(config, ["transform", "box-shadow", "border-color", "background-color"]);
}

/**
 * Checks prefers-reduced-motion. Safe for SSR (returns false on server).
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia === "undefined") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * Returns motion-safe duration: 0 if reduced motion preferred, otherwise bounded duration.
 */
export function getSafeDuration(duration: MotionDuration): number {
  if (prefersReducedMotion()) return 0;
  return motionDurations[duration];
}

/**
 * Bounded tilt calculation helper. Ensures rotation stays within limits.
 */
export function calculateBoundedTilt(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  maxRotationDeg = 8
): { rotateX: number; rotateY: number } {
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;

  const deltaX = clientX - centerX;
  const deltaY = clientY - centerY;

  // Normalize to -1..1
  const normX = rect.width > 0 ? deltaX / (rect.width / 2) : 0;
  const normY = rect.height > 0 ? deltaY / (rect.height / 2) : 0;

  // Clamp
  const clampedX = Math.max(-1, Math.min(1, normX));
  const clampedY = Math.max(-1, Math.min(1, normY));

  // Bounded rotation
  const rotateY = clampedX * maxRotationDeg;
  const rotateX = -clampedY * maxRotationDeg;

  // Final clamp to ensure never exceeds max, normalize -0 to 0
  const clampedRotateX = Math.max(-maxRotationDeg, Math.min(maxRotationDeg, rotateX));
  const clampedRotateY = Math.max(-maxRotationDeg, Math.min(maxRotationDeg, rotateY));
  return {
    rotateX: Object.is(clampedRotateX, -0) ? 0 : clampedRotateX,
    rotateY: Object.is(clampedRotateY, -0) ? 0 : clampedRotateY,
  };
}

/**
 * Creates a throttled callback using requestAnimationFrame for performance.
 * Returns a function that should be called on pointer move.
 */
export function createRafThrottle<T extends (...args: unknown[]) => void>(callback: T): T {
  let rafId: number | null = null;
  let lastArgs: unknown[] | null = null;

  const throttled = (...args: unknown[]) => {
    lastArgs = args;
    if (rafId !== null) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      if (lastArgs) {
        callback(...(lastArgs as Parameters<T>));
        lastArgs = null;
      }
    });
  };

  return throttled as unknown as T;
}
