import { forwardRef, type ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

/** Small shadcn-style local primitive: behavior stays native while visual variants stay centralized. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className = "", variant = "secondary", type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`button button--${variant} ${className}`.trim()}
      {...props}
    />
  );
});
