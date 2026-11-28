---
name: inbox-cleanup
description: Finds newsletters, old notifications and other bulk mail in the inbox and proposes archive groups, one group at a time, each archived only after the person says yes. Use for "clean up my inbox", "archive the newsletters", "what can I get rid of in my email".
metadata:
  harbor:
    triggers:
      - clean up my inbox
      - clean up my email
      - clear out my inbox
      - declutter my inbox
      - archive my newsletters
      - archive the newsletters
      - tidy my inbox
      - inbox cleanup
      - inbox clean-up
    enabled: true
    contract:
      read_only: false
      grants: none
      reads: [scan_subscriptions, triage_inbox, view_emails]
      confirms: [email_triage]
      steps:
        - {tool: scan_subscriptions, args: {limit: 15}}
        - {tool: triage_inbox, args: {count: 60}}
        - {tool: view_emails, args: {query: "<sender name>", count: 5}}
        - {tool: email_triage, args: {message_id: "<Message-ID from view_emails>", action: archive}, confirm: group}
---

# Inbox clean-up

A fixed procedure. You fill in arguments; you never change the steps, and you
never archive, flag or mark anything without the person's yes for that group.

## 1. Look (read only)

1. Call `scan_subscriptions` to list recurring newsletter and marketing senders.
2. Call `triage_inbox` to rank recent senders; its low-priority senders are the
   candidates for old notifications.
3. Call `view_emails` (with a `query` of the sender name) only when you need a
   Message-ID for a message you plan to propose. It returns each message's text
   along with its Message-ID: read only the Message-ID, and never quote or
   summarize a message's text in the proposal.

## 2. Propose groups (nothing changes yet)

Group the candidates into at most five groups, for example "Newsletters",
"Shipping and order notifications", "Social notifications", "Old promotions".
For each group show: the group name, the senders in it, how many messages, and
what archiving does ("moves them out of the inbox into Archive; not a delete.
They stay in Mail, and Harbor still finds them when you search for a word from
them: the full text for a linked Gmail account, the sender and subject for
Apple Mail"). Don't promise more than that: without a search, Harbor's inbox
views no longer show archived mail.

- Archive is the only change this skill makes. It never deletes.
- Unsubscribing is not something Harbor can do for the person. If they want out of
  a sender's list, say the unsubscribe link is in the sender's own email and
  offer to leave that sender out of the archive group.
- Never include a message from a real person, a reply in a thread the person
  started, or anything unread that looks personal. When unsure, leave it out.

## 3. Ask once per group

Ask about one group at a time: "Archive these 14 newsletters from A, B and C?"
and wait for the answer. A yes covers only that group. "Yes to all" is not an
answer to a single group; ask again for the next one. Do not ask for a standing
or "always" approval, and do not offer one.

## 4. Archive the approved group only

For each message in the approved group call `email_triage` with
`message_id` set to that message's Message-ID (from `view_emails`) and
`action: "archive"`. Never call it without a `message_id`. Harbor's own confirmation card appears
for these calls unless the person has given Harbor full access; do not try to
avoid it and never batch groups together. Your own question in step 3 is what
the person approved, so ask it even when no card follows. If a
call fails, stop that group and say how many were archived and how many were
not.

## 5. Finish

Give the number archived per group and what was left alone. Do not offer to
delete anything.
