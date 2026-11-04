You write a cover letter for the candidate, for the job below. It must read like the candidate wrote it, and it
must be written for THIS company and THIS role: a letter that could be sent to another company unchanged is a failure.

## What the letter says
1. Why this role and this company interest the candidate. Use concrete details from the job posting (what the
   company builds, who its users are, the problems the team works on, how they work, their stack) and connect
   each one to what the candidate has actually done or is looking for (see "About the candidate" and the
   experience bank). Name the company, and mention at least one concrete detail that only fits this posting.
   No generic flattery ("your innovative company", "a leader in the field").
2. The two or three experiences from the bank that matter most for this role, saying concretely what the
   candidate built and how it relates to the job.
3. A short, plain close (availability or wanting to talk), then the sign-off with the candidate's name ({name}).

## Rules
- Facts about the candidate come only from the experience bank and "About the candidate". Never invent a project,
  a technology, a number or a result. Facts about the company come only from the job posting; never present the
  company's technologies or products as the candidate's own experience.
- Write as the candidate, in the first person, in their voice: match the writing samples (sentence length,
  phrasing, how they open and structure things, plain words). No "I am excited to apply", no buzzwords, no em dashes.
- Correct spelling and grammar, but keep their voice.
- Write in the language of the job posting.
- Do not repeat what the candidate already wrote in this application's form answers (below): the recruiter reads
  both. Use different experiences or a different angle, and never reuse their sentences.
- At most {max_words} words. Start with a greeting to the company's team. No address block and no date: the
  template adds them.

Return JSON: {"letter": "<the letter, paragraphs separated by blank lines>"}

## The job
Company: {company}
Title: {job_title}
Description:
{description}

## The candidate's answers in this application's form (do not repeat them)
{answers}

## About the candidate (what they are looking for and care about)
{candidate}

## The candidate's writing samples (their own words, typos included)
{samples}

## Answers the candidate recently sent (question → their final answer)
{recent}

## The candidate's notes about their style
{notes}

## The experience bank (YAML): the only source of facts about the candidate
{bank}
