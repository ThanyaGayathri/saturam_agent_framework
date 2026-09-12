export default function Input({
  label,
  required,
  placeholder,
  icon,
  type = "text",
  className = "",
  inputClassName = "",
  ...props
}) {
  return (
    <div className={className}>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-text-primary">
          {label}
          {required && <span className="text-red-500"> *</span>}
        </label>
      )}
      <div className="relative">
        {icon && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">
            {icon}
          </span>
        )}
        <input
          type={type}
          placeholder={placeholder}
          className={`w-full rounded-lg border border-border-light bg-surface py-2 text-sm text-text-primary placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary ${
            icon ? "pl-10 pr-4" : "px-4"
          } ${inputClassName}`}
          {...props}
        />
      </div>
    </div>
  );
}
