// File: components/enterprise/GlassButton.tsx — Primary/secondary/ghost enterprise glass button with loading and disabled states, focus ring and keyboard behavior

"use client";

import React, { forwardRef, useState } from "react";
import { getButtonAriaProps } from "@/lib/enterprise-ui/accessibility";

export type GlassButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type GlassButtonSize = "sm" | "md" | "lg";

export interface GlassButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: GlassButtonVariant;
  readonly size?: GlassButtonSize;
  readonly loading?: boolean;
  readonly loadingLabel?: string;
  readonly leftIcon?: React.ReactNode;
  readonly rightIcon?: React.ReactNode;
}

const sizeStyles: Record<GlassButtonSize, { padding: string; fontSize: string; radius: string; height: number }> = {
  sm: { padding: "6px 12px", fontSize: "12px", radius: "var(--enterprise-radius-sm)", height: 32 },
  md: { padding: "10px 18px", fontSize: "13px", radius: "var(--enterprise-radius-md)", height: 40 },
  lg: { padding: "12px 24px", fontSize: "14px", radius: "var(--enterprise-radius-md)", height: 48 },
};

const variantStyles: Record<GlassButtonVariant, { bg: string; hoverBg: string; border: string; color: string; shadow: string }> = {
  primary: {
    bg: "linear-gradient(135deg, #7c5cfc 0%, #5b3fd8 100%)",
    hoverBg: "linear-gradient(135deg, #6d4af0 0%, #4f35c0 100%)",
    border: "rgba(124, 92, 252, 0.32)",
    color: "#ffffff",
    shadow: "0 4px 16px rgba(124, 92, 252, 0.32), 0 0 0 1px rgba(124, 92, 252, 0.24)",
  },
  secondary: {
    bg: "rgba(255,255,255,0.08)",
    hoverBg: "rgba(255,255,255,0.12)",
    border: "rgba(255,255,255,0.14)",
    color: "var(--enterprise-text)",
    shadow: "0 2px 12px rgba(0,0,0,0.16), 0 0 0 1px rgba(255,255,255,0.08)",
  },
  ghost: {
    bg: "transparent",
    hoverBg: "rgba(255,255,255,0.06)",
    border: "transparent",
    color: "var(--enterprise-text-secondary)",
    shadow: "none",
  },
  danger: {
    bg: "linear-gradient(135deg, #ef4444 0%, #b91c1c 100%)",
    hoverBg: "linear-gradient(135deg, #dc2626 0%, #991b1b 100%)",
    border: "rgba(239, 68, 68, 0.32)",
    color: "#ffffff",
    shadow: "0 4px 16px rgba(239, 68, 68, 0.24)",
  },
};

export const GlassButton = forwardRef<HTMLButtonElement, GlassButtonProps>(function GlassButton(
  {
    variant = "primary",
    size = "md",
    loading = false,
    loadingLabel = "Loading",
    leftIcon,
    rightIcon,
    disabled,
    children,
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

  const sizeStyle = sizeStyles[size];
  const variantStyle = variantStyles[variant];
  const isDisabled = disabled || loading;

  const ariaProps = getButtonAriaProps({
    label: typeof children === "string" ? (children as string) : loading ? loadingLabel : "Button",
    disabled: isDisabled,
    loading,
  });

  return (
    <button
      ref={ref}
      type="button"
      disabled={isDisabled}
      data-variant={variant}
      data-size={size}
      data-loading={loading ? "true" : "false"}
      className={["enterprise-glass-button", "enterprise-focus-ring", className].filter(Boolean).join(" ")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: sizeStyle.padding,
        height: sizeStyle.height,
        borderRadius: sizeStyle.radius,
        background: isHovered && !isDisabled ? variantStyle.hoverBg : variantStyle.bg,
        border: `1px solid ${variantStyle.border}`,
        color: variantStyle.color,
        fontSize: sizeStyle.fontSize,
        fontWeight: 600,
        lineHeight: 1,
        cursor: isDisabled ? "not-allowed" : "pointer",
        opacity: isDisabled ? 0.6 : 1,
        boxShadow: variantStyle.shadow,
        transform: isPressed && !isDisabled ? "translateY(1px)" : isHovered && !isDisabled ? "translateY(-1px)" : "none",
        transition: "transform 120ms ease, background 200ms ease, box-shadow 200ms ease, border-color 200ms ease, opacity 200ms ease",
        backdropFilter: variant === "secondary" ? "blur(16px)" : undefined,
        WebkitBackdropFilter: variant === "secondary" ? "blur(16px)" : undefined,
        whiteSpace: "nowrap",
        userSelect: "none",
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
        <>
          <span
            aria-hidden="true"
            style={{
              width: 14,
              height: 14,
              border: "2px solid currentColor",
              borderTopColor: "transparent",
              borderRadius: "50%",
              display: "inline-block",
              animation: "enterprise-spin 0.8s linear infinite",
            }}
          />
          <span>{loadingLabel}</span>
        </>
      ) : (
        <>
          {leftIcon && (
            <span aria-hidden="true" style={{ display: "inline-flex", alignItems: "center" }}>
              {leftIcon}
            </span>
          )}
          <span>{children}</span>
          {rightIcon && (
            <span aria-hidden="true" style={{ display: "inline-flex", alignItems: "center" }}>
              {rightIcon}
            </span>
          )}
        </>
      )}
      <style>{`
        @keyframes enterprise-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </button>
  );
});

GlassButton.displayName = "GlassButton";
