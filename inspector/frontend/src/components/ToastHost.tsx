import { SystemAlert } from '@reportportal/ui-kit';

import { useApp } from '../app/state';

const TOAST_MS = 2200;

/**
 * Bottom-center toast. The kit ships a single alert, not a manager, so the
 * placement and stacking are ours: one alert at a time, same as before.
 */
export function ToastHost() {
  const { toastMessage, dismissToast } = useApp();
  if (!toastMessage) return null;
  return (
    <div
      className="toast-host"
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        bottom: '22px',
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 90,
      }}
    >
      {/* `type` defaults to info in the kit, which is what this toast is. */}
      <SystemAlert title={toastMessage} duration={TOAST_MS} onClose={dismissToast} />
    </div>
  );
}
