You write a cover letter for the candidate, for the job below. It must read like the candidate wrote it.

- Write as the candidate, in the first person. Match the writing samples: their sentence length, their phrasing,
  how they open and structure what they say, their plain words. Not a polished template letter: no "I am
  excited to apply", no buzzwords, no em dashes.
- Correct spelling and grammar, but keep their voice.
- Use only facts from the experience bank. Never invent a project, a technology, a number or a result.
- Pick the two or three experiences that matter most for this job, and say concretely what the candidate did.
- Write in the language of the job posting.
- At most {max_words} words. Start with a greeting to the company's team, end with a short sign-off and the
  candidate's name ({name}). No address block, no date: the template adds them.

Return JSON: {"letter": "<the letter, paragraphs separated by blank lines>"}

## The job
Company: {company}
Title: {job_title}
Description:
{description}

## The candidate's writing samples (their own words, typos included)
{samples}

## Answers the candidate recently sent (question → their final answer)
{recent}

## The candidate's notes about their style
{notes}

## The experience bank (YAML): the only source of facts
{bank}
