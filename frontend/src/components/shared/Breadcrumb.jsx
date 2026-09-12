import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

/**
 * Simple static breadcrumb trail.
 * @param {{ items: { label: string, to?: string }[] }} props
 * Last item should have no `to` - it renders as plain text (current page).
 */
export default function Breadcrumb({ items }) {
 return (
 <nav aria-label="Breadcrumb" className="mb-5 flex items-center gap-1 text-sm">
 {items.map((item, i) => {
 const isLast = i === items.length - 1;
 return (
 <span key={i} className="flex items-center gap-1">
 {i > 0 && <ChevronRight size={13} className="shrink-0 text-text-muted" aria-hidden />}
 {isLast || !item.to ? (
 <span className="font-medium text-text-primary">{item.label}</span>
 ) : (
 <Link
 to={item.to}
 className="text-text-secondary transition-colors hover:text-text-primary"
 >
 {item.label}
 </Link>
 )}
 </span>
 );
 })}
 </nav>
 );
}
