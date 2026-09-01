import { AlertTriangle, Loader2, X } from "lucide-react";

export function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

const BUTTON_VARIANTS = {
  primary: "bg-brand text-white hover:bg-brand-light",
  secondary: "bg-white text-gray-800 border border-gray-200 hover:bg-gray-50",
  ghost: "text-gray-700 hover:bg-gray-100",
  danger: "bg-red-600 text-white hover:bg-red-700",
};

const BUTTON_SIZES = {
  sm: "text-xs px-2.5 py-1.5",
  md: "text-sm px-3.5 py-2",
  icon: "p-2",
};

export function Button({ variant = "primary", size = "md", className, children, ...props }) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors",
        "disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40",
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

const FIELD =
  "w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand/50 disabled:bg-gray-50";

export function Input({ className, ...props }) {
  return <input className={cx(FIELD, className)} {...props} />;
}

export function Textarea({ className, ...props }) {
  return <textarea className={cx(FIELD, "min-h-28 resize-y", className)} {...props} />;
}

export function Select({ className, children, ...props }) {
  return (
    <select className={cx(FIELD, className)} {...props}>
      {children}
    </select>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-400">{hint}</span>}
    </label>
  );
}

export function Toggle({ checked, onChange, label, description }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-3 text-left"
    >
      <span
        className={cx(
          "relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors",
          checked ? "bg-brand" : "bg-gray-300",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </span>
      <span>
        <span className="block text-sm font-medium text-gray-800">{label}</span>
        {description && <span className="block text-xs text-gray-500">{description}</span>}
      </span>
    </button>
  );
}

export function Spinner({ className }) {
  return <Loader2 className={cx("animate-spin text-gray-400", className)} size={18} aria-label="Loading" />;
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-gray-500">
      <Spinner /> {label}
    </div>
  );
}

const TONES = {
  gray: "bg-gray-100 text-gray-700",
  brand: "bg-brand-dim text-brand",
  green: "bg-green-50 text-green-700",
  amber: "bg-amber-50 text-amber-700",
  red: "bg-red-50 text-red-700",
  blue: "bg-blue-50 text-blue-700",
};

export function Badge({ tone = "gray", className, children }) {
  return (
    <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function ErrorBanner({ error, onRetry, onDismiss, className }) {
  if (!error) return null;
  const message = typeof error === "string" ? error : error.message || "Something went wrong";
  return (
    <div className={cx("flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800", className)}>
      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
      <span className="flex-1 break-words">{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="font-medium underline">
          Retry
        </button>
      )}
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="text-red-500 hover:text-red-700">
          <X size={14} />
        </button>
      )}
    </div>
  );
}

export function Notice({ notice, onDismiss }) {
  if (!notice) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
      <span className="flex-1">{notice}</span>
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="text-green-600 hover:text-green-800">
          <X size={14} />
        </button>
      )}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, hint, action }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-gray-500">
      {Icon && <Icon size={36} className="text-gray-300" />}
      <div className="text-sm font-medium text-gray-700">{title}</div>
      {hint && <div className="max-w-sm text-xs">{hint}</div>}
      {action}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide = false }) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
        className={cx("flex max-h-[90vh] w-full flex-col rounded-xl bg-white shadow-xl", wide ? "max-w-3xl" : "max-w-xl")}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 text-gray-500 hover:bg-gray-100">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-gray-100 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-3">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {subtitle && <p className="text-xs text-gray-500">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}
