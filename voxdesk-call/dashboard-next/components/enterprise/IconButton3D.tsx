// File: components/enterprise/IconButton3D.tsx — Compact accessible glass icon button with depth/hover interaction and tooltip-ready labeling, no icon-only unlabeled controls

"use client";

import React, { forwardRef, useState } from "react";
import { getButtonAriaProps } from "@/lib/enterprise-ui/accessibility";

export type IconButtonSize = "sm" | "md" | "lg";
export type IconButtonVariant = "glass" | "ghost" | "solid";

export interface IconButton3DProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  readonly ariaLabel: string;
  readonly icon: React.ReactNode;
  readonly size?: IconButtonSize;
  readonly variant?: IconButtonVariant;
  readonly tooltip?: string;
  readonly loading?: boolean;
  readonly active?: boolean;
}

const sizeMap: Record<IconButtonSize, { size: number; fontSize: number; radius: string }> = {
  sm: { size: 32, fontSize: 14, radius: "var(--enterprise-radius-sm)" },
  md: { size: 40, fontSize: 16, radius: "var(--enterprise-radius-md)" },
  lg: { size: 48, fontSize: 18, radius: "var(--enterprise-radius-md)" },
};

const variantMap: Record<IconButtonVariant, { bg: string; border: string; color: string; hoverBg: string }> = {
  glass: {
    bg: "rgba(255,255,255,0.06)",
    border: "rgba(255,255,255,0.10)",
    color: "var(--enterprise-text)",
    hoverBg: "rgba(255,255,255,0.10)",
  },
  ghost: {
    bg: "transparent",
    border: "transparent",
    color: "var(--enterprise-text-secondary)",
    hoverBg: "rgba(255,255,255,0.06)",
  },
  solid: {
    bg: "var(--enterprise-accent-3d)",
    border: "rgba(124, 92, 252, 0.3)",
    color: "#ffffff",
    hoverBg: "linear-gradient(135deg, #6d4af0 0%, #4f35c0 100%)",
  },
};

export const IconButton3D = forwardRef<HTMLButtonElement, IconButton3DProps>(function IconButton3D(
  {
    ariaLabel,
    icon,
    size = "md",
    variant = "glass",
    tooltip,
    loading = false,
    active = false,
    disabled,
    className,
    style,
    onMouseEnter,
    onMouseLeave,
    ...rest
  },
  ref
) {
  const [isHovered, setIsHovered] = useState(false);
  const [isPressed, setIsPressed] = useState(false);
  const sizeConfig = sizeMap[size];
  const variantConfig = variantMap[variant];

  const isDisabled = disabled || loading;

  const ariaProps = getButtonAriaProps({
    label: ariaLabel,
    disabled: isDisabled,
    loading,
    pressed: active,
  });

  return (
    <button
      ref={ref}
      type="button"
      aria-label={ariaLabel}
      title={tooltip ?? ariaLabel}
      disabled={isDisabled}
      data-active={active ? "true" : "false"}
      data-loading={loading ? "true" : "false"}
      className={["enterprise-icon-button-3d", "enterprise-focus-ring", className].filter(Boolean).join(" ")}
      style={{
        width: sizeConfig.size,
        height: sizeConfig.size,
        borderRadius: sizeConfig.radius,
        background: isHovered && !isDisabled ? variantConfig.hoverBg : variantConfig.bg,
        border: `1px solid ${variantConfig.border}`,
        color: variantConfig.color,
        display: "inline-grid",
        placeItems: "center",
        cursor: isDisabled ? "not-allowed" : "pointer",
        fontSize: sizeConfig.fontSize,
        fontWeight: 600,
        lineHeight: 1,
        padding: 0,
        position: "relative",
        transformStyle: "preserve-3d",
        transform: isPressed && !isDisabled ? "translateY(1px) translateZ(0)" : isHovered && !isDisabled ? "translateY(-1px) translateZ(8px)" : "translateZ(0)",
        boxShadow: active
          ? "0 0 0 2px var(--enterprise-accent-glass), var(--enterprise-accent-glow)"
          : isHovered && !isDisabled
            ? "0 8px 20px rgba(0,0,0,0.32), 0 0 0 1px rgba(255,255,255,0.10)"
            : "0 2px 8px rgba(0,0,0,0.16), 0 0 0 1px rgba(255,255,255,0.06)",
        transition: "transform 200ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 200ms ease, background 200ms ease, border-color 200ms ease",
        opacity: isDisabled ? 0.5 : 1,
        backdropFilter: variant === "glass" ? "blur(16px)" : undefined,
        WebkitBackdropFilter: variant === "glass" ? "blur(16px)" : undefined,
        ...style,
      }}
      onMouseEnter={(e) => {
        setIsHovered(true);
        onMouseEnter?.(e);
      }}
      onMouseLeave={(e) => {
        setIsHovered(false);
        setIsPressed(false);
        onMouseLeave?.(e);
      }}
      onMouseDown={() => setIsPressed(true)}
      onMouseUp={() => setIsPressed(false)}
      {...ariaProps}
      {...rest}
    >
      {loading ? (
        <span
          aria-hidden="true"
          style={{
            width: sizeConfig.fontSize,
            height: sizeConfig.fontSize,
            border: "2px solid currentColor",
            borderTopColor: "transparent",
            borderRadius: "50%",
            display: "inline-block",
            animation: "enterprise-spin 0.8s linear infinite",
          }}
        />
      ) : (
        icon
      )}
      <style>{`
        @keyframes enterprise-spin {
          to { transform: rotate(360deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          .enterprise-icon-button-3d {
            transform: none !important;
          }
        }
      `}</style>
    </button>
  );
});

IconButton3D.displayName = "IconButton3D";
