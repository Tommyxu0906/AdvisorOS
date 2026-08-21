/**
 * Conversations, held in memory for the session.
 *
 * No database and no persistence, for the same reason the API key is not persisted: this is a
 * consultation about someone's balance sheet, and the least surprising place for it to live is
 * nowhere. A refresh loses it, which is the intended trade until there is a reason to change it.
 *
 * Each conversation carries its own advisor selection. That is the part worth being deliberate
 * about — asking Buffett and Munger about a concentrated position and asking Bogle and Housel
 * about an emergency fund are different consultations, and forcing one global committee across
 * both would make the transcript incoherent.
 */

import type { ChatTurn } from "../types";

export interface Conversation {
  id: string;
  title: string;
  advisorIds: string[];
  turns: ChatTurn[];
  createdAt: number;
}

/** The pair the product demonstrates with. Both are hand-authored built-ins. */
export const DEFAULT_ADVISORS = ["buffett", "munger"];

export function newConversation(advisorIds: string[] = DEFAULT_ADVISORS): Conversation {
  return {
    id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`,
    title: "New consultation",
    advisorIds: [...advisorIds],
    turns: [],
    createdAt: Date.now(),
  };
}

/**
 * Name a conversation from its first question.
 *
 * Truncated on a word boundary rather than mid-word: a sidebar full of "Should I reduce my NV…"
 * reads worse than one full of slightly shorter, complete phrases.
 */
export function titleFrom(question: string): string {
  const clean = question.trim().replace(/\s+/g, " ");
  if (clean.length <= 42) return clean;
  const cut = clean.slice(0, 42);
  const lastSpace = cut.lastIndexOf(" ");
  return `${lastSpace > 20 ? cut.slice(0, lastSpace) : cut}…`;
}

/** History in the shape the consult endpoint expects. Marker turns carry no text and are dropped. */
export function historyFor(conversation: Conversation) {
  return conversation.turns
    .filter((t) => !t.assumption)
    .map((t) => ({
      role: t.role,
      text: t.text,
      advisor_responses: t.advisor_responses as unknown[],
    }));
}
