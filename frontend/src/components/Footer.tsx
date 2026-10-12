import { APP_NAME, GITHUB_URL, GithubIcon } from '../site';

export default function Footer() {
  return (
    <footer className="foot">
      <span>{APP_NAME} · MIT</span>
      <span aria-hidden="true">·</span>
      <a href={GITHUB_URL} target="_blank" rel="noreferrer">
        <GithubIcon size={13} />
        <span>GitHub</span>
      </a>
    </footer>
  );
}
