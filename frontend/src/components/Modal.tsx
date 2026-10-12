import { ReactNode } from 'react';
import { createPortal } from 'react-dom';

// Dialogs render straight into document.body so no page container
// (animations, overflow, transforms) can ever trap or clip them.
export default function Modal({
  onClose,
  width,
  children,
}: {
  onClose: () => void;
  width?: number;
  children: ReactNode;
}) {
  return createPortal(
    <div className="modal-back" onClick={onClose}>
      <div
        className="modal"
        style={width ? { width } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
