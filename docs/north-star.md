# North star

What Lachesis and the factory are for, told as outcomes from the author's side. Specs say
precisely what the system does and are checked by acceptance tests; these stories say what
good looks like and are checked by the north-star audit, which compares them with the specs
and the backlog and files what is missing. Every spec change should name the story it serves.

## Principles

- The author decides what the projects should be; machines decide whether the code meets it.
  No human reviews or merges code.
- The author's attention is the scarcest resource. The factory asks only what only the author
  can decide, says it once, and keeps working on everything else while it waits.
- Tokens are the budget. Work is sized, priced from history, planned to fit the week, and the
  week is reviewed against the plan.
- The factory keeps itself running: it notices when it is stuck, fixes what it can, and
  escalates the rest in one place.

## Stories

### 1. Away for two weeks

I leave for two weeks with several features in flight. Halfway through, from my phone, I ask
Claude for something new and agree the details in that conversation.

When I come back:

- The new request is built, or visibly in progress: what I agreed in conversation counted as
  decided, became a spec change that merged by itself, and its work ranked ahead of the
  routine backlog without me setting focus.
- The features in flight have landed, except where they truly needed me. Small questions the
  factory could settle with a sensible default were settled, and I can see which defaults it
  took. Questions that change what a project should be waited for me, and are listed in one
  place, oldest first, each stating the decision in one line.
- Each week ended with one sprint review in my notifications: what shipped, points planned
  against delivered, what it cost per account and model, which estimates were wrong and what
  audits found.
- The factory never sat idle because one account ran dry while another had allowance it was
  allowed to use, and it never spent beyond the shares and caps I set.
- Nothing is stuck silently: anything the factory could not fix itself is in the morning
  briefing, once, with what it needs from me.
