---
name: ppt-writing
description: Draft presentation outlines, slide-by-slide content, titles, bullet points, and speaker notes for PPT or slide deck tasks. Use when the user asks for a presentation, courseware, report deck, project summary, defense slides, business proposal, or any request to organize content into slide pages.
---

# PPT Writing

Build the deck structure before polishing wording.

## Workflow

1. Identify the presentation goal, audience, and time limit.
2. Choose a deck structure that matches the goal.
3. Draft slide titles first.
4. Fill each slide with 3-5 concise bullets.
5. Add speaker notes only when they help delivery or timing.
6. Check that the story flows from problem to conclusion.

## Default Deck Patterns

Use one of these patterns unless the user already provides a structure.

### Project Report

1. Title
2. Background
3. Goal or problem statement
4. Approach or plan
5. Progress or findings
6. Risks or blockers
7. Next steps
8. Closing or Q&A

### Business Proposal

1. Title
2. Current problem
3. Opportunity
4. Proposed solution
5. Benefits or value
6. Timeline
7. Budget or resources
8. Call to action

### Academic or Course Presentation

1. Title
2. Topic background
3. Research question or objective
4. Method or framework
5. Key analysis
6. Conclusion
7. Limitations
8. Q&A

## Slide Writing Rules

- Keep one main idea per slide.
- Prefer short bullets over dense paragraphs.
- Use concrete nouns and active verbs.
- Put conclusions before detail when presenting to decision-makers.
- Avoid more than 5 bullets on one slide unless the user asks for dense notes.
- If data is missing, write a clear placeholder such as `[insert sales chart]`.

## Output Formats

Choose the lightest format that satisfies the request.

- For a quick request: return a slide list with title and bullets.
- For a fuller request: return `Slide 1`, `Slide 2` style sections with titles, bullets, and optional speaker notes.
- For rewrite requests: preserve the user's existing structure and improve clarity, order, and emphasis.

## Helpful Additions

Add these only when useful:

- Opening sentence for the presenter
- Closing sentence or call to action
- Visual suggestion such as chart, timeline, matrix, or comparison table
- Short speaking note for transitions between slides

## Quality Check

Before finishing, verify:

- The first two slides establish context quickly.
- Middle slides support the main argument instead of repeating it.
- The final slide ends with a decision, summary, or next step.
- Wording matches the audience level: student, manager, client, or executive.
