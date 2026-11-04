You prepare a job application for the candidate. For every form field below, decide how it is filled:

- `fact`: answered by one of the candidate's profile facts. Give `profile_key`, one of: {profile_keys}.
  For a field with options, also give `option`: the option that matches that fact, copied exactly.
- `open`: a question the candidate answers in their own words. Give `answer`. For a field with options
  that is not a profile fact (e.g. "Are you open to working in the office?"), give `option` instead,
  copied exactly from its options, and choose what the experience bank and the profile support.
- `leave`: anything you cannot answer truthfully from the profile and the experience bank, and every
  question about gender, ethnicity, race, disability, veteran status or sexual orientation, and every
  agreement, consent or terms checkbox. The candidate fills those in themselves.

Return one item per field, using the field's `key`.

## How to write `open` answers
Write as the candidate, in the first person, so it reads like the candidate wrote it:
- Match the writing samples and recent answers below: their sentence length, their phrasing, how they
  open and structure an answer, their plain words. Do not sound like a polished cover letter. No
  buzzwords, no "I am excited to", no em dashes.
- Correct spelling and grammar (fix "its"/"it's" and similar), but keep their voice.
- Use only facts from the experience bank. Never invent a project, a technology, a number or a result.
- Answer in the language of the question.
- Respect each field's limit. Without a stated limit, stay under {default_words} words.
- Each open answer uses different material: do not repeat the same project, example or sentence in two answers.
  If two questions overlap, answer the second from a different angle or with a different experience.
- Do not repeat the cover letter below either: the recruiter reads both, so the answers add what the letter
  does not say.
{ai_ban_rule}

## The job
Company: {company}
Title: {job_title}
Description:
{description}

## The form fields (JSON)
{fields}

## The candidate's cover letter for this job (do not repeat it)
{cover_letter}

## The candidate's writing samples (their own words, typos included)
{samples}

## Answers the candidate recently sent (question → their final answer)
{recent}

## The candidate's notes about their style
{notes}

## The experience bank (YAML): the only source of facts
{bank}
