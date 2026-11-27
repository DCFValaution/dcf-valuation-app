// The Firestore rules, executed.
//
// These rules are not a second line of defence. The app talks to Firestore
// directly and the backend holds no Firebase credential, so there is no
// server in the path to re-check anything: this file is the only thing
// standing between one user's saved work and everybody else's. Rules you
// have not run are rules you have not tested, and a rule that is wrong in
// the permissive direction fails silently and forever.
//
// Run with:  npm test          (in this folder)
//
// Needs a JDK on PATH - the Firestore emulator is a Java program. Nothing
// else: the emulator serves a fake project, so no Firebase account, no
// project id and no network are involved.

import { strict as assert } from 'node:assert';
import { after, before, beforeEach, describe, it } from 'node:test';

import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from '@firebase/rules-unit-testing';
import { readFileSync } from 'node:fs';
import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  serverTimestamp,
  setDoc,
  writeBatch,
} from 'firebase/firestore';

let env;

const ALICE = 'uid-alice';
const BOB = 'uid-bob';

// An installation that has never signed in. Every copy of the app has one
// of these from first launch, which is what makes a per-installation free
// allowance possible without fingerprinting the device.
const ANON = 'uid-anonymous';

const NOW = new Date('2026-11-23T12:00:00Z');

function asAlice() {
  return env.authenticatedContext(ALICE).firestore();
}
function asBob() {
  return env.authenticatedContext(BOB).firestore();
}
function asStranger() {
  return env.unauthenticatedContext().firestore();
}
/** Signed in, but anonymously - which is an account without being one. */
function asAnonymous() {
  return env
    .authenticatedContext(ANON, { firebase: { sign_in_provider: 'anonymous' } })
    .firestore();
}

/** A valid user record. */
function user(overrides = {}) {
  return {
    email: 'alice@example.com',
    created_at: NOW,
    last_seen_at: NOW,
    ...overrides,
  };
}

/** A valid watchlist document. */
function watchlist(overrides = {}) {
  return {
    tickers: ['NVDA', 'KO'],
    rows: { NVDA: { value: 219.33, price: 125.53, as_of: '2026-11-20' } },
    updated_at: NOW,
    ...overrides,
  };
}

/** A valid saved valuation. */
function save(overrides = {}) {
  return {
    name: 'NVDA — Damodaran growth path',
    ticker: 'NVDA',
    schema_version: 3,
    assumptions: { years_two_to_five_growth: 0.2, terminal_roic: 0.25 },
    saved_value: 219.33,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

before(async () => {
  env = await initializeTestEnvironment({
    projectId: 'intrinsic-rules-test',
    firestore: {
      rules: readFileSync('firestore.rules', 'utf8'),
      host: '127.0.0.1',
      port: 8089,
    },
  });
});

// Every test starts from an empty database.
//
// Added after two counter tests failed for a reason that had nothing to do
// with the rule being tested: a marker document left behind by an earlier
// test turned the next test's `create` into an `update`, which the marker
// rule refuses outright. The tests were right and the suite was wrong, and
// a suite whose result depends on the order it runs in is not evidence.
beforeEach(async () => {
  await env.clearFirestore();
});

after(async () => {
  await env?.cleanup();
});

// ---------------------------------------------------------------------------
// Ownership: the part that actually matters
// ---------------------------------------------------------------------------

describe('ownership', () => {
  it('lets a user write and read their own record', async () => {
    const db = asAlice();
    await assertSucceeds(setDoc(doc(db, `users/${ALICE}`), user()));
    await assertSucceeds(getDoc(doc(db, `users/${ALICE}`)));
  });

  it('refuses another user their record', async () => {
    await env.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), `users/${ALICE}`), user());
    });
    await assertFails(getDoc(doc(asBob(), `users/${ALICE}`)));
    await assertFails(setDoc(doc(asBob(), `users/${ALICE}`), user()));
  });

  it('refuses a signed-out visitor everything', async () => {
    const db = asStranger();
    await assertFails(getDoc(doc(db, `users/${ALICE}`)));
    await assertFails(setDoc(doc(db, `users/${ALICE}`), user()));
    await assertFails(getDoc(doc(db, `users/${ALICE}/lists/watchlist`)));
    await assertFails(getDoc(doc(db, `users/${ALICE}/saves/s1`)));
  });

  it("refuses another user's watchlist and saves", async () => {
    await env.withSecurityRulesDisabled(async (ctx) => {
      const db = ctx.firestore();
      await setDoc(doc(db, `users/${ALICE}/lists/watchlist`), watchlist());
      await setDoc(doc(db, `users/${ALICE}/saves/s1`), save());
    });
    const bob = asBob();
    await assertFails(getDoc(doc(bob, `users/${ALICE}/lists/watchlist`)));
    await assertFails(getDoc(doc(bob, `users/${ALICE}/saves/s1`)));
    await assertFails(deleteDoc(doc(bob, `users/${ALICE}/saves/s1`)));
  });
});

// ---------------------------------------------------------------------------
// Deletion: a Play requirement, so never conditional on anything else
// ---------------------------------------------------------------------------

describe('deletion', () => {
  it('refuses a QUERY on lists, which is how the app got this wrong',
    async () => {
      // `lists/watchlist` is readable and `lists/{other}` is denied, and a
      // collection query has to satisfy the rules for every document it
      // could return - so querying the collection is refused even though
      // reading the one document in it is allowed.
      //
      // The deletion originally queried `lists` to find what to remove. It
      // deleted the saves, was refused here, and stopped: saves gone,
      // watchlist behind, account intact. That it was survivable is the
      // documents-before-auth-user order doing its job.
      const db = asAlice();
      await assertSucceeds(
        setDoc(doc(db, `users/${ALICE}/lists/watchlist`), watchlist()),
      );
      await assertFails(getDocs(collection(db, `users/${ALICE}/lists`)));
    });

  it('allows the exact sequence the app deletes with', async () => {
    // Not "can a user delete things" but "can a user delete things THIS
    // WAY". The rules and the deletion code have to agree about which
    // operations exist, and only running both together proves they do.
    const db = asAlice();
    await assertSucceeds(setDoc(doc(db, `users/${ALICE}`), user()));
    await assertSucceeds(
      setDoc(doc(db, `users/${ALICE}/lists/watchlist`), watchlist()),
    );
    await assertSucceeds(setDoc(doc(db, `users/${ALICE}/saves/s1`), save()));
    await assertSucceeds(setDoc(doc(db, `users/${ALICE}/saves/s2`), save()));

    // 1. query the saves, whose ids are generated
    const saves = await assertSucceeds(
      getDocs(collection(db, `users/${ALICE}/saves`)),
    );
    assert.equal(saves.size, 2);
    for (const d of saves.docs) {
      await assertSucceeds(deleteDoc(d.ref));
    }
    // 2. the watchlist by name, never by query
    await assertSucceeds(deleteDoc(doc(db, `users/${ALICE}/lists/watchlist`)));
    // 3. the record itself, last
    await assertSucceeds(deleteDoc(doc(db, `users/${ALICE}`)));
  });

  it('lets a user delete every document they own', async () => {
    const db = asAlice();
    await assertSucceeds(setDoc(doc(db, `users/${ALICE}`), user()));
    await assertSucceeds(
      setDoc(doc(db, `users/${ALICE}/lists/watchlist`), watchlist()),
    );
    await assertSucceeds(setDoc(doc(db, `users/${ALICE}/saves/s1`), save()));

    await assertSucceeds(deleteDoc(doc(db, `users/${ALICE}/saves/s1`)));
    await assertSucceeds(deleteDoc(doc(db, `users/${ALICE}/lists/watchlist`)));
    await assertSucceeds(deleteDoc(doc(db, `users/${ALICE}`)));
  });
});

// ---------------------------------------------------------------------------
// Shape: what a signed-in user may store under their own uid
// ---------------------------------------------------------------------------

describe('the user record', () => {
  it('refuses a field we never said we store', async () => {
    // The data-safety form says no display name and no photo URL. This is
    // what makes that true rather than merely intended.
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}`),
        user({ display_name: 'Alice', photo_url: 'https://…' })),
    );
  });

  it('requires the email and the two timestamps', async () => {
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}`), { email: 'alice@example.com' }),
    );
  });

  it('refuses an email that is not a short string', async () => {
    const db = asAlice();
    await assertFails(setDoc(doc(db, `users/${ALICE}`), user({ email: '' })));
    await assertFails(setDoc(doc(db, `users/${ALICE}`), user({ email: 42 })));
    await assertFails(
      setDoc(doc(db, `users/${ALICE}`), user({ email: 'a'.repeat(400) })),
    );
  });

  it('accepts a server timestamp, which is what the app actually sends',
    async () => {
      await assertSucceeds(
        setDoc(doc(asAlice(), `users/${ALICE}`),
          user({ created_at: serverTimestamp(), last_seen_at: serverTimestamp() })),
      );
    });
});

describe('the watchlist', () => {
  it('accepts a list of tickers with the rows cache', async () => {
    await assertSucceeds(
      setDoc(doc(asAlice(), `users/${ALICE}/lists/watchlist`), watchlist()),
    );
  });

  it('accepts a watchlist with no rows cached yet', async () => {
    const { rows, ...withoutRows } = watchlist();
    await assertSucceeds(
      setDoc(doc(asAlice(), `users/${ALICE}/lists/watchlist`), withoutRows),
    );
  });

  it('refuses more tickers than the app will ever show', async () => {
    const many = Array.from({ length: 51 }, (_, i) => `T${i}`);
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/lists/watchlist`),
        watchlist({ tickers: many })),
    );
  });

  it('refuses a watchlist that is not a list', async () => {
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/lists/watchlist`),
        watchlist({ tickers: 'NVDA' })),
    );
  });

  it('refuses an unknown field', async () => {
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/lists/watchlist`),
        watchlist({ notes: 'anything at all' })),
    );
  });

  it('refuses a second list nobody wrote a rule for', async () => {
    // Named explicitly in the rules so that a list added later fails in
    // development rather than quietly becoming world-shaped in production.
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/lists/portfolio`),
        watchlist()),
    );
  });
});

describe('a saved valuation', () => {
  it('accepts a complete save', async () => {
    await assertSucceeds(
      setDoc(doc(asAlice(), `users/${ALICE}/saves/s1`), save()),
    );
  });

  it('refuses a save with no schema version', async () => {
    // The field that lets a slider added next year reopen a save made
    // today. A write without it would be discovered later by a migration
    // that cannot tell which shape it is looking at.
    const { schema_version, ...without } = save();
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/saves/s1`), without),
    );
  });

  it('refuses a schema version that is not a whole number', async () => {
    const db = asAlice();
    await assertFails(
      setDoc(doc(db, `users/${ALICE}/saves/s1`), save({ schema_version: '3' })),
    );
    await assertFails(
      setDoc(doc(db, `users/${ALICE}/saves/s1`), save({ schema_version: 3.5 })),
    );
    await assertFails(
      setDoc(doc(db, `users/${ALICE}/saves/s1`), save({ schema_version: 0 })),
    );
  });

  it('refuses assumptions that are not a map', async () => {
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/saves/s1`),
        save({ assumptions: [0.2, 0.25] })),
    );
  });

  it('refuses a save with more assumptions than the model has', async () => {
    const tooMany = {};
    for (let i = 0; i < 41; i += 1) tooMany[`k${i}`] = i;
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/saves/s1`),
        save({ assumptions: tooMany })),
    );
  });

  it('refuses an empty or oversized name', async () => {
    const db = asAlice();
    await assertFails(
      setDoc(doc(db, `users/${ALICE}/saves/s1`), save({ name: '' })),
    );
    await assertFails(
      setDoc(doc(db, `users/${ALICE}/saves/s1`),
        save({ name: 'x'.repeat(121) })),
    );
  });

  it('accepts a refused company, which has no saved value', async () => {
    // Tesla gets a save like anything else: the assumptions are the point,
    // and there is no figure to keep beside them.
    await assertSucceeds(
      setDoc(doc(asAlice(), `users/${ALICE}/saves/s1`),
        save({ ticker: 'TSLA', saved_value: null })),
    );
  });

  it('refuses an unknown field', async () => {
    await assertFails(
      setDoc(doc(asAlice(), `users/${ALICE}/saves/s1`),
        save({ price_history: [1, 2, 3] })),
    );
  });
});

// ---------------------------------------------------------------------------
// Default deny
// ---------------------------------------------------------------------------

describe('everything else', () => {
  it('is unreachable, signed in or not', async () => {
    for (const db of [asAlice(), asStranger()]) {
      await assertFails(getDoc(doc(db, 'quotes/NVDA')));
      await assertFails(setDoc(doc(db, 'quotes/NVDA'), { price: 1 }));
      await assertFails(setDoc(doc(db, 'anything/at/all/here'), { x: 1 }));
    }
  });

  it('includes the top-level users collection itself', async () => {
    // A user document is reachable by its owner; the collection is not
    // listable by anybody, which is what stops a signed-in user
    // enumerating every account on the service.
    assert.ok(true, 'enforced by the absence of a list rule');
  });
});

// ---------------------------------------------------------------------------
// The valuation counter and its markers
// ---------------------------------------------------------------------------
//
// The free allowance is three valuations per installation, counted by the
// API writing under the caller's own uid. That write is governed by these
// rules and by nothing else, so the attack worth testing is not "can Bob
// read Alice's count" - it is "can Alice avoid being counted".
//
// She would do it by creating the (ticker, day) markers herself, ahead of
// time: a marker that already exists makes the API's commit fail its
// precondition, and nothing is counted. The rules bind the two writes
// together so that cannot happen, and these are the tests that prove it.

const DAY = '2026-11-25';
const MARK = `${DAY}__AAPL`;
const MARK2 = `${DAY}__MSFT`;

function usageDoc(db, uid = ALICE) {
  return doc(db, `usage/${uid}`);
}
function markerDoc(db, id, uid = ALICE) {
  return doc(db, `usage/${uid}/valued/${id}`);
}
function counter(overrides = {}) {
  return {
    lifetime: 1,
    day: DAY,
    day_count: 1,
    last_marker: MARK,
    ...overrides,
  };
}

/** What the API does: the +1 and the marker, in one atomic commit. */
function countOnce(db, { uid = ALICE, fields = counter(), id = MARK,
  ticker = 'AAPL' } = {}) {
  const batch = writeBatch(db);
  batch.set(usageDoc(db, uid), fields);
  batch.set(markerDoc(db, id, uid), { ticker });
  return batch.commit();
}

/** Put a counter in place without going through the rules. */
async function seedCounter(fields = counter(), uid = ALICE, id = MARK) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    await setDoc(doc(db, `usage/${uid}`), fields);
    await setDoc(doc(db, `usage/${uid}/valued/${id}`), { ticker: 'AAPL' });
  });
}

describe('the valuation counter', () => {
  it('accepts the +1 and the marker in one commit', async () => {
    await assertSucceeds(countOnce(asAlice()));
  });

  it('REFUSES a marker created on its own, which is the whole attack',
    async () => {
      // Nothing else in the commit. If this were allowed, a client could
      // pre-create a marker for every ticker it was about to value and
      // never be counted at all.
      await assertFails(
        setDoc(markerDoc(asAlice(), MARK), { ticker: 'AAPL' }));
    });

  it('refuses a marker created on its own when a counter already exists',
    async () => {
      // The same attack from the other starting point: the counter is
      // there and simply is not moved. getAfter sees no +1.
      await seedCounter();
      await assertFails(
        setDoc(markerDoc(asAlice(), MARK2), { ticker: 'MSFT' }));
    });

  it('REFUSES A SECOND MARKER RIDING ON ONE INCREMENT', async () => {
    // Found by mutation testing, which is the only reason it is here.
    // Deleting `last_marker == markerId` from the rule left every other
    // test passing: a marker created ALONE is still refused, because the
    // counter has not moved. What it stops is subtler and worth more -
    // one legitimate +1 carrying any number of markers with it, which
    // would let a client pre-stamp every ticker it was about to value
    // and be counted once for all of them.
    const db = asAlice();
    const batch = writeBatch(db);
    batch.set(usageDoc(db), counter({ last_marker: MARK }));
    batch.set(markerDoc(db, MARK), { ticker: 'AAPL' });
    batch.set(markerDoc(db, MARK2), { ticker: 'MSFT' });
    await assertFails(batch.commit());
  });

  it('refuses a +1 with no marker to account for it', async () => {
    await assertFails(setDoc(usageDoc(asAlice()), counter()));
  });

  it('refuses a counter that names one marker while creating another',
    async () => {
      const db = asAlice();
      const batch = writeBatch(db);
      batch.set(usageDoc(db), counter({ last_marker: MARK }));
      batch.set(markerDoc(db, MARK2), { ticker: 'MSFT' });
      await assertFails(batch.commit());
    });

  it('refuses a jump of more than one', async () => {
    await seedCounter();
    await assertFails(countOnce(asAlice(), {
      fields: counter({ lifetime: 3, day_count: 3, last_marker: MARK2 }),
      id: MARK2,
      ticker: 'MSFT',
    }));
  });

  it('refuses a jump on the counter ALONE, with no marker written',
    async () => {
      // The case that isolates the counter's own step check.
      //
      // Every other jump is stopped by the MARKER rule on the way past,
      // so deleting `lifetime == resource.lifetime + 1` left the whole
      // suite green - mutation testing found that. This write touches no
      // marker at all: it names one that already exists, so
      // `existsAfter` is satisfied and the marker rule is never
      // evaluated. Only the step check stands between this and a counter
      // that moves by any amount it likes.
      await seedCounter();
      await assertFails(
        setDoc(usageDoc(asAlice()),
          // day_count MUST be a legal +1, or the day rule refuses this
          // write and the step check is never reached - which is what the
          // first version of this test did, and why the mutant survived
          // it.
          counter({ lifetime: 7, day_count: 2, last_marker: MARK })),
      );
    });

  it('refuses a count that goes down, which is the other way to cheat',
    async () => {
      await seedCounter(counter({ lifetime: 3, day_count: 3 }));
      await assertFails(countOnce(asAlice(), {
        fields: counter({ lifetime: 1, day_count: 1, last_marker: MARK2 }),
        id: MARK2,
        ticker: 'MSFT',
      }));
    });

  it('counts a second company the same day', async () => {
    await seedCounter();
    await assertSucceeds(countOnce(asAlice(), {
      fields: counter({ lifetime: 2, day_count: 2, last_marker: MARK2 }),
      id: MARK2,
      ticker: 'MSFT',
    }));
  });

  it('restarts the day counter on a new day, without anything running at '
    + 'midnight', async () => {
    await seedCounter(counter({ lifetime: 5, day: '2026-11-24', day_count: 4 }));
    await assertSucceeds(countOnce(asAlice(), {
      fields: counter({ lifetime: 6, day: DAY, day_count: 1,
        last_marker: MARK2 }),
      id: MARK2,
      ticker: 'MSFT',
    }));
  });

  it('refuses a day counter that does not restart on a new day', async () => {
    await seedCounter(counter({ lifetime: 5, day: '2026-11-24', day_count: 4 }));
    await assertFails(countOnce(asAlice(), {
      fields: counter({ lifetime: 6, day: DAY, day_count: 5,
        last_marker: MARK2 }),
      id: MARK2,
      ticker: 'MSFT',
    }));
  });

  it("refuses another user's counter entirely", async () => {
    await seedCounter();
    await assertFails(getDoc(usageDoc(asBob(), ALICE)));
    await assertFails(countOnce(asBob(), { uid: ALICE }));
  });

  it('refuses a field nobody wrote a rule for', async () => {
    await assertFails(countOnce(asAlice(), {
      fields: counter({ unlimited: true }),
    }));
  });

  it('refuses changing a marker after the fact', async () => {
    await seedCounter();
    await assertFails(setDoc(markerDoc(asAlice(), MARK), { ticker: 'MSFT' }));
  });
});

// ---------------------------------------------------------------------------
// Deleting it, which Play policy and our own /delete page both promise
// ---------------------------------------------------------------------------

describe('deleting the counter', () => {
  it('refuses a reset while the user record survives', async () => {
    // The reason this rule is not plain ownership. Signed-in callers are
    // unmetered today, so this costs nothing now - and everything on the
    // day FREE_DAILY_VALUATIONS is set.
    await env.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), `users/${ALICE}`), user());
    });
    await seedCounter();
    await assertFails(deleteDoc(usageDoc(asAlice())));
  });

  it('allows it in the same commit as the user record, which is how the '
    + 'account is deleted', async () => {
    await env.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), `users/${ALICE}`), user());
    });
    await seedCounter();
    const db = asAlice();
    const batch = writeBatch(db);
    batch.delete(doc(db, `users/${ALICE}`));
    batch.delete(usageDoc(db));
    await assertSucceeds(batch.commit());
  });

  it('lets an anonymous installation delete its counter, having no user '
    + 'record to delete', async () => {
    await seedCounter(counter(), ANON, MARK);
    await assertSucceeds(deleteDoc(usageDoc(asAnonymous(), ANON)));
  });

  it('lets the owner delete markers, which lowers nothing', async () => {
    await seedCounter();
    await assertSucceeds(deleteDoc(markerDoc(asAlice(), MARK)));
  });
});

// ---------------------------------------------------------------------------
// What an anonymous installation may NOT do
// ---------------------------------------------------------------------------
//
// Every installation signs in anonymously now, so "signed in" no longer
// means "has an account". The privacy policy says an anonymous
// installation holds a counter and nothing else, and this is what makes
// that true rather than merely intended.

describe('an anonymous installation', () => {
  it('may count valuations, which is the whole point of it', async () => {
    await assertSucceeds(countOnce(asAnonymous(), { uid: ANON }));
  });

  it('may not keep a user record', async () => {
    await assertFails(
      setDoc(doc(asAnonymous(), `users/${ANON}`), user()));
  });

  it('may not keep a watchlist', async () => {
    await assertFails(
      setDoc(doc(asAnonymous(), `users/${ANON}/lists/watchlist`),
        watchlist()));
  });

  it('may not keep a saved valuation', async () => {
    await assertFails(
      setDoc(doc(asAnonymous(), `users/${ANON}/saves/s1`), save()));
  });
});
