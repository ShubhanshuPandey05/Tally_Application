/*
 * The name, as the wide form of the mark: heavy "Prime", light "Analyst", in
 * Sora.
 *
 * The square mark (the favicon, the app's launcher icon) is the same name
 * stacked on a tile with a blue bar under it, generated to outlines by
 * `tools/brand`. It is not repeated here: a tile that already says
 * "Prime Analyst" beside the words "Prime Analyst" is the name twice.
 */
export default function Logo() {
  return (
    <span className="logo" aria-label="Prime Analyst">
      <span className="logo-word">
        <b>Prime</b> Analyst
      </span>
    </span>
  );
}
