import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "./Icons";

export function Dialog({
  title,
  open,
  onClose,
  busy = false,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  busy?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = `dialog-${title.replaceAll(" ", "-")}`;
  useEffect(() => {
    if (open && !ref.current?.open) ref.current?.showModal();
    if (!open && ref.current?.open) ref.current.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="dialog-header">
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="Закрыть"
        >
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
