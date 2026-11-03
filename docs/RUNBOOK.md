# Running the game — one page

For whoever is holding the organizer's phone. You do not need to know anything
about the software.

## Before people arrive

1. On your phone, open the game's address with **/admin** on the end, for example
   **http://192.168.1.20:8000/admin** (whoever set it up has the address).
2. Type the organizer password (the `BARGAMES_ADMIN_PASSWORD` set during setup). You stay signed in.
3. Check the three settings. The defaults are fine:
   - **Seconds per question** — 15
   - **Questions per round** — 8
   - **Reveal seconds** — 5 (how long the answer stays on screen)
4. Tap **New game**. A big 4-digit PIN appears. That PIN is the whole game.

## Getting people in

Tell them either one:
- Go to the game's address (for example **http://192.168.1.20:8000**) and type the PIN, or
- Tap the link under the PIN on your screen (send it to the group chat — it has
  the PIN built in, so they skip typing it).

They enter a name and can take a selfie. **The selfie is optional** — skipping it
gives them a coloured circle with their initial, and everything still works.

Watch them appear on your screen as they join.

## Starting

Tap **Start the game** once everyone is in.

- If nobody has joined yet it will refuse and say so. That is deliberate — a game
  started with an empty room would burn through the questions.
- Anyone who joins **after** you start is let in, but waits for the next question
  rather than being dropped into one already running.

## During a round

Everyone sees the same question at the same time, and each phone gets its own
15 seconds from the moment the question appears on *that* phone — so someone on
slow signal is not punished for it.

**Nobody's score moves until everyone has answered.** Once they have, the answer
is revealed and everyone scores at once, the leaderboard moves, and the next question
comes up on its own. You do not tap through the questions.

While a question is open, your screen shows everyone as a bubble — bright with a
tick means answered, dim and pulsing means still thinking. That is how you see
who the room is waiting for.

### The one button you may need: END QUESTION

If someone's phone has died, they have wandered off, or the room is just waiting
too long — tap **End question**. Everyone who answered keeps their points,
everyone who did not gets zero, and the game moves on immediately.

You will not usually need it. The game also un-sticks itself: it moves on once
everyone's own timer has run out, and there is a hard cap so one dead phone can
never hold the room.

## Between rounds

After the last question of a round, everyone sees the leaderboard. Tap
**Next round** when you are ready. After the final round, that is the game.

## Close calls

If someone typed an answer that was *nearly* right ("Otawa" for
"Ottawa"), it scores — and shows up on your screen under **Close calls**
with a Revoke button. Only you see this. Revoke it if it was too generous; you
can change your mind either way and the scores update live.

## The photos

Guests' selfies never leave the server, are only visible to people in the game,
and delete themselves 24 hours after the game. If you want them gone sooner, the
**Delete every photo now** button at the bottom of your screen does it
immediately and cannot be undone.

## If something goes wrong

| What you see | What to do |
|---|---|
| A phone is stuck on an old screen | Pull down to refresh. Nothing is lost — score and place come straight back. |
| Someone closed the tab / their phone died | They reopen the link they joined with. Same player, same score. |
| Someone lost the link entirely | They join again with the PIN. They come in as a new player on zero — there is no way around that. |
| The room is waiting on one person | **End question**. |
| Your own phone dies | Open the admin page on any other phone and sign in again. Nothing is tied to the device. |
| The whole thing looks dead | Wait ten seconds — phones reconnect by themselves. If it is still dead, ask whoever runs the computer to restart the app; the game survives a restart. |

**Keep the admin page open on a second device if you can.** It costs nothing and
it is the one thing with no backup.
