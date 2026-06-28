# /dunsteel-email — Draft Email in Nathan's Voice

Draft a professional email for Nathan Hancock, Project Manager at Dunsteel.

## Variables

context: $ARGUMENTS

---

## Instructions

### Step 1 — Load Context

Read:
- `context/tone-of-voice.md` — Nathan's voice rules and real email examples
- `context/pm-context.md` — active projects and key contacts (for accurate names and details)

If `context/tone-of-voice.md` has no real email examples yet (placeholders only), note this and draft using construction-industry professional tone. Prompt Nathan at the end: "Add real email examples to `context/tone-of-voice.md` and I'll calibrate better next time."

### Step 2 — Understand the Request

Parse `$ARGUMENTS` for:
- **Purpose:** What is this email trying to achieve?
- **Recipient:** Who is it going to? Match against pm-context.md contacts if possible.
- **Situation:** What happened / what is the context?
- **Tone:** Firm, neutral, collaborative, chasing, declining, escalating?

If the context is too thin to write a good email, ask one clarifying question before drafting. Do not ask more than one.

### Step 3 — Draft the Email

**Apply the `writing-style` skill** to the email body. Run its self-check protocol before output. The skill covers em dashes, banned words, banned phrases, and structural pattern bans. `context/tone-of-voice.md` overrides the skill where there's conflict (Nathan's actual voice is authoritative for these emails).

Draft following the tone rules in `context/tone-of-voice.md`. Match Nathan's:
- Sentence length and structure
- Level of formality (professional, direct, not stiff)
- How he opens and closes
- How he handles difficult or sensitive topics

Format:
```
Subject: [subject line]

[email body]
```

Include a note if any attachment should be referenced (e.g. programme, drawing, certificate).

### Step 4 — Output

Present the draft. Then ask:
> "Want me to adjust the tone, shorten it, or change anything?"

Do not save the output — Nathan copies it directly into Outlook.
