You write a cover letter for the candidate, for the job below. It must read like the candidate wrote it, and it
must be written for THIS company and THIS role: a letter that could be sent to another company unchanged is a failure.

## How the letter works
Read the posting and work out what the team really needs underneath it: the problem they are trying to solve,
the kind of person who would make it easier. Then write about that in the candidate's own words, the way they
would explain it to a friend, and let their experience come in naturally while they talk about it: what they
built, what problem it solved, what they learned, and why that makes this role a natural next step for them.

It is one flowing piece of writing, not a checklist. Do NOT walk through the posting point by point, do NOT quote
it or reuse its phrases, and do NOT use the pattern "you are looking for X; I have done X".

Bad (mechanical, echoes the posting):
  "You are looking for an engineer with experience building RAG systems. I have built RAG pipelines.
   You want someone who can work with customers. I have presented to clients."
Good (rephrased, experience woven in; style example only, these are not the candidate's facts):
  "What I like in this role is that the hard part isn't the model, it's getting it to answer from a company's
   own data without making things up. That's most of what I've been doing this year: ..."

## What it covers
1. Why this role and this company interest the candidate: what the team is trying to do, said in the
   candidate's words, and why it connects to what they have done or are looking for (see "About the candidate").
   Name the company. Be specific to this job, without copying the posting.
2. The two or three experiences from the bank that matter most here, told as part of that story: what the
   candidate built and why it is relevant.
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
