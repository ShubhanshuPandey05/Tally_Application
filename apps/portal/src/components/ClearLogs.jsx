import { useState } from 'react';
import { Modal, Segmented, useToast } from './ui.jsx';

/**
 * The "clear this" button, and the dialog that makes sure it was meant.
 *
 * Shared by all three log surfaces because the decision behind the button is
 * the same one every time — how far back to go, and whether the person really
 * meant the whole thing — and three copies of that would drift into three
 * different ideas of what "clear" removes.
 *
 * Retention already ages these tables out on its own. This exists for the case
 * retention cannot help with: one chatty machine filling the table today, and
 * somebody who wants the space back now rather than in two days.
 */

//: Offered windows. "Everything" is last and never preselected — the default
//: has to be the narrow choice, because the dialog is dismissed by people who
//: have already decided and are not reading it closely.
const WINDOWS = [
  { value: '1', label: 'Older than a day' },
  { value: '0', label: 'Up to now' },
  { value: 'all', label: 'Everything' },
];

export default function ClearLogs({
  label = 'Clear',
  title,
  subject,
  note,
  disabled = false,
  onClear,
  onDone,
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [window, setWindow] = useState('1');
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const result = await onClear(window === 'all' ? null : Number(window));
      // The server says what it removed. Reporting the count rather than a bare
      // "done" is what separates a clear that worked from one that matched
      // nothing because a filter was still on.
      toast(
        result?.deleted
          ? `${result.deleted.toLocaleString()} rows removed — ${result.scope || subject}.`
          : 'Nothing matched, so nothing was removed.',
      );
      setOpen(false);
      onDone?.();
    } catch (failure) {
      toast(failure.message, 'bad');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className="btn btn-sm btn-danger"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>

      {open ? (
        <Modal
          title={title || 'Clear logs'}
          subtitle={subject}
          onClose={() => !busy && setOpen(false)}
          footer={
            <>
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button
                className="btn btn-danger"
                type="button"
                disabled={busy}
                onClick={run}
              >
                {busy ? 'Clearing…' : 'Delete them'}
              </button>
            </>
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Deleted rows are not recoverable. Choose how far back to go.
          </p>
          <Segmented
            ariaLabel="How far back to clear"
            options={WINDOWS}
            value={window}
            onChange={setWindow}
          />
          {note ? (
            <p className="dim" style={{ marginBottom: 0 }}>
              {note}
            </p>
          ) : null}
        </Modal>
      ) : null}
    </>
  );
}
