import { pretty } from '../api';

// Human-readable result first, full JSON one click away.
export default function ResultBlock({ text, raw }: { text: string; raw: unknown }) {
  return (
    <>
      <pre className="out">{text}</pre>
      <details style={{ marginTop: 8 }}>
        <summary className="muted" style={{ cursor: 'pointer', fontSize: 12 }}>
          Raw JSON
        </summary>
        <pre className="out" style={{ marginTop: 8 }}>
          {pretty(raw)}
        </pre>
      </details>
    </>
  );
}
