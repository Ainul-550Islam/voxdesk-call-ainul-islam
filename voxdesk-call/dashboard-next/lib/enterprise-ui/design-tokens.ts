// File: lib/enterprise-ui/design-tokens.ts — Strongly typed design token definitions for spacing, radii, elevation, translucency, motion, typography and visual density for the enterprise AI platform

/**
 * VoxDesk Enterprise AI Platform — Design Tokens
 * Framework-independent, strongly typed constants.
 * No backend logic, no fake data, no secrets.
 */

export type SpacingScale = {
  readonly xxs: string;
  readonly xs: string;
  readonly sm: string;
  readonly md: string;
  readonly lg: string;
  readonly xl: string;
  readonly xxl: string;
  readonly xxxl: string;
};

export type RadiiScale = {
  readonly xs: string;
  readonly sm: string;
  readonly md: string;
  readonly lg: string;
  readonly xl: string;
  readonly xxl: string;
  readonly full: string;
};

export type ElevationLevel = 0 | 1 | 2 | 3 | 4 | 5;

export type GlassAlpha = {
  readonly subtle: number;
  readonly low: number;
  readonly medium: number;
  readonly high: number;
  readonly opaque: number;
};

export type BlurLevel = {
  readonly none: string;
  readonly sm: string;
  readonly md: string;
  readonly lg: string;
  readonly xl: string;
  readonly xxl: string;
};

export type ShadowToken = {
  readonly elevation: ElevationLevel;
  readonly css: string;
};

export type ZIndexScale = {
  readonly base: number;
  readonly elevated: number;
  readonly overlay: number;
  readonly modal: number;
  readonly popover: number;
  readonly tooltip: number;
  readonly command: number;
};

export type DurationScale = {
  readonly instant: string;
  readonly fast: string;
  readonly normal: string;
  readonly slow: string;
  readonly slower: string;
};

export type BreakpointScale = {
  readonly sm: number;
  readonly md: number;
  readonly lg: number;
  readonly xl: number;
  readonly xxl: number;
};

export type TypographyScale = {
  readonly fontFamily: {
    readonly sans: string;
    readonly mono: string;
  };
  readonly fontSize: {
    readonly xs: string;
    readonly sm: string;
    readonly md: string;
    readonly lg: string;
    readonly xl: string;
    readonly xxl: string;
    readonly displaySm: string;
    readonly displayMd: string;
    readonly displayLg: string;
  };
  readonly fontWeight: {
    readonly regular: number;
    readonly medium: number;
    readonly semibold: number;
    readonly bold: number;
    readonly extrabold: number;
  };
  readonly lineHeight: {
    readonly tight: number;
    readonly normal: number;
    readonly relaxed: number;
  };
  readonly letterSpacing: {
    readonly tight: string;
    readonly normal: string;
    readonly wide: string;
  };
};

export type DensityMode = "compact" | "comfortable" | "spacious";

export type DesignTokens = {
  readonly spacing: SpacingScale;
  readonly radii: RadiiScale;
  readonly glassAlpha: GlassAlpha;
  readonly blur: BlurLevel;
  readonly shadows: readonly ShadowToken[];
  readonly zIndex: ZIndexScale;
  readonly duration: DurationScale;
  readonly breakpoints: BreakpointScale;
  readonly typography: TypographyScale;
  readonly density: Record<DensityMode, { gap: string; padding: string }>;
};

export const spacing: SpacingScale = {
  xxs: "4px",
  xs: "8px",
  sm: "12px",
  md: "16px",
  lg: "24px",
  xl: "32px",
  xxl: "48px",
  xxxl: "64px",
} as const;

export const radii: RadiiScale = {
  xs: "6px",
  sm: "10px",
  md: "14px",
  lg: "20px",
  xl: "28px",
  xxl: "36px",
  full: "9999px",
} as const;

export const glassAlpha: GlassAlpha = {
  subtle: 0.03,
  low: 0.05,
  medium: 0.08,
  high: 0.12,
  opaque: 0.8,
} as const;

export const blur: BlurLevel = {
  none: "none",
  sm: "blur(8px)",
  md: "blur(16px)",
  lg: "blur(24px)",
  xl: "blur(32px)",
  xxl: "blur(48px) saturate(180%)",
} as const;

export const shadows: readonly ShadowToken[] = [
  { elevation: 0, css: "none" },
  {
    elevation: 1,
    css: "0 1px 2px rgba(0,0,0,0.12), 0 0 0 1px rgba(255,255,255,0.06)",
  },
  {
    elevation: 2,
    css: "0 4px 16px rgba(0,0,0,0.24), 0 0 0 1px rgba(255,255,255,0.08), inset 0 1px 0 rgba(255,255,255,0.08)",
  },
  {
    elevation: 3,
    css: "0 8px 32px rgba(0,0,0,0.36), 0 0 0 1px rgba(255,255,255,0.10), inset 0 1px 0 rgba(255,255,255,0.10)",
  },
  {
    elevation: 4,
    css: "0 16px 48px rgba(0,0,0,0.48), 0 0 0 1px rgba(255,255,255,0.12), inset 0 1px 0 rgba(255,255,255,0.12)",
  },
  {
    elevation: 5,
    css: "0 24px 64px rgba(0,0,0,0.60), 0 0 0 1px rgba(255,255,255,0.14), inset 0 1px 0 rgba(255,255,255,0.14)",
  },
] as const;

export const zIndex: ZIndexScale = {
  base: 0,
  elevated: 10,
  overlay: 20,
  modal: 30,
  popover: 40,
  tooltip: 50,
  command: 60,
} as const;

export const duration: DurationScale = {
  instant: "0ms",
  fast: "120ms",
  normal: "200ms",
  slow: "320ms",
  slower: "480ms",
} as const;

export const breakpoints: BreakpointScale = {
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
  xxl: 1536,
} as const;

export const typography: TypographyScale = {
  fontFamily: {
    sans: "'Inter', ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
    mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  },
  fontSize: {
    xs: "11px",
    sm: "12px",
    md: "13px",
    lg: "15px",
    xl: "18px",
    xxl: "24px",
    displaySm: "28px",
    displayMd: "36px",
    displayLg: "48px",
  },
  fontWeight: {
    regular: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
    extrabold: 800,
  },
  lineHeight: {
    tight: 1.1,
    normal: 1.5,
    relaxed: 1.7,
  },
  letterSpacing: {
    tight: "-0.02em",
    normal: "0",
    wide: "0.04em",
  },
} as const;

export const density: Record<DensityMode, { gap: string; padding: string }> = {
  compact: { gap: spacing.xs, padding: spacing.sm },
  comfortable: { gap: spacing.md, padding: spacing.lg },
  spacious: { gap: spacing.xl, padding: spacing.xxl },
} as const;

export const designTokens: DesignTokens = {
  spacing,
  radii,
  glassAlpha,
  blur,
  shadows,
  zIndex,
  duration,
  breakpoints,
  typography,
  density,
} as const;

export function getShadow(elevation: ElevationLevel): string {
  const found = shadows.find((s) => s.elevation === elevation);
  return found ? found.css : shadows[0].css;
}

export function getBreakpointQuery(minWidth: number): string {
  return `(min-width: ${minWidth}px)`;
}
