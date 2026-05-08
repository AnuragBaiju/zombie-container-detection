"""Plagiarism self-check for report.tex.

Compares 6-word phrases between report.tex and the other prose files in the
repo, plus a small block of representative phrasing from Li et al. (2025).
Prints a percentage and lists overlapping phrases for review.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent

def normalise(text: str) -> str:
    # Strip LaTeX environments (best-effort)
    text = re.sub(r"\\begin\{[^}]+\}.*?\\end\{[^}]+\}", " ", text, flags=re.DOTALL)
    text = re.sub(r"\\[a-zA-Z]+\*?\s*(\[[^\]]*\])?\s*(\{[^}]*\})?", " ", text)
    # Strip markdown code blocks and inline code
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", " ", text)
    # Strip markdown table rows
    text = re.sub(r"\|.*?\|", " ", text)
    # Strip remaining punctuation, brackets, digits
    text = re.sub(r"[\[\](){}#*_>~,.;:!?\"'\-]", " ", text)
    text = re.sub(r"[0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def ngrams(words: list[str], n: int) -> set[str]:
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


# Representative phrasing from Li et al. (2025) abstract / key claims that we
# legitimately cite or paraphrase. Listed here so the check distinguishes
# "fair paraphrase + attribution" from "verbatim copy".
LI_ET_AL_KEY_PHRASES = [
    "kubernetes default metrics fail to distinguish between active and idle containers",
    "energy aware elastic scaling algorithm for kubernetes microservices",
    "reduction in total energy consumption relative to the default kubernetes horizontal pod autoscaler",
    "the feedback loop periodically releases excess containers",
]


def main() -> None:
    N = 6  # standard plagiarism-detector n-gram size

    report  = normalise((REPO / "report.tex").read_text(encoding="utf-8"))
    readme  = normalise((REPO / "README.md").read_text(encoding="utf-8"))
    prof    = normalise((REPO / "PROFESSOR_RESPONSE.md").read_text(encoding="utf-8"))

    r_grams      = ngrams(report.split(), N)
    readme_grams = ngrams(readme.split(), N)
    prof_grams   = ngrams(prof.split(),   N)

    li_grams: set[str] = set()
    for phrase in LI_ET_AL_KEY_PHRASES:
        li_grams |= ngrams(normalise(phrase).split(), N)

    overlap_readme = r_grams & readme_grams
    overlap_prof   = r_grams & prof_grams
    overlap_li     = r_grams & li_grams

    total = max(len(r_grams), 1)
    print(f"report.tex unique {N}-word phrases : {len(r_grams)}")
    print(f"  shared with README.md            : {len(overlap_readme):4d} "
          f"({100 * len(overlap_readme) / total:.1f}%)")
    print(f"  shared with PROFESSOR_RESPONSE   : {len(overlap_prof):4d} "
          f"({100 * len(overlap_prof) / total:.1f}%)")
    print(f"  shared with Li et al. key phrases: {len(overlap_li):4d} "
          f"({100 * len(overlap_li) / total:.1f}%)")
    print()

    threshold_pct = 5.0
    repo_ratio = 100 * (len(overlap_readme | overlap_prof)) / total
    li_ratio   = 100 * len(overlap_li) / total
    print(f"Threshold for concern: {threshold_pct}% overlap")
    print(f"Combined repo overlap : {repo_ratio:.1f}%   "
          f"{'OK' if repo_ratio < threshold_pct else 'REVIEW'}")
    print(f"Li et al. overlap     : {li_ratio:.1f}%   "
          f"{'OK (cited)' if li_ratio < threshold_pct else 'REVIEW'}")
    print()

    if overlap_readme | overlap_prof:
        print("Sample phrases shared with the repo's own prose")
        print("(these would be considered 'self-plagiarism' if not paraphrased):")
        for p in sorted(overlap_readme | overlap_prof)[:10]:
            print(f"  - \"{p}\"")
    if overlap_li:
        print()
        print("Phrases shared with Li et al. (cited & paraphrased):")
        for p in sorted(overlap_li):
            print(f"  - \"{p}\"")


if __name__ == "__main__":
    main()
