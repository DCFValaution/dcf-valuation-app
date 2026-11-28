# What's new

Notes written for the people who use Intrinsic, not for the people who
build it. Plain language, no file names, and every entry says what it
means for a figure on screen rather than what changed in the code.

The engineering record is the git history; `PARKED.md` holds what was
decided against and why.

---

## 28 November 2026

**Some valuations have moved, and a few by a lot.**

Intrinsic shows a company's return on capital — how much profit it earns
on the money tied up in the business — in the "Worth knowing" box. It
also assumes a long-run return on capital when working out what the
company is worth after the forecast ends.

Those two were being measured in different ways. The figure on screen
used what the business owns and uses, averaged over three years. The one
inside the model used a narrower measure from a single year, which
flatters companies that have bought back a lot of their own shares.

They now use the same measure: the one shown on screen. Where the two
disagreed, the valuation changes — usually downward, because the
measure on screen is the more demanding of the two, and a lower return
means more profit has to be reinvested to keep growing.

Most companies barely move. The ones that do:

- **3M** and **Amazon** each fall about **11%**
- **Johnson & Johnson** about 4%
- **Coca-Cola**, **ExxonMobil** and **Microsoft** about 2–3%
- **Apple**, **Nvidia** and **Palantir** do not move at all, because both
  measures put them above the ceiling the model applies anyway

Nothing about these companies changed. The measurement did, and it is
now the one you can see.

**A company that comes out worth nothing is now told to you plainly.**

Occasionally the model produces a company whose entire value — the
forecast years and everything after them together — comes out at zero or
below, because the investment it is assumed to make outweighs the cash it
brings in. Intrinsic refused some of these already, but by a rule that
could not see this particular case, so a few slipped through and were
reported as ordinary valuations.

They are now refused, with a plain explanation rather than a percentage
that would not have meant anything. As with every refusal, you can still
open the hypothetical screen and set the assumptions yourself to see what
would have to be true for the company to be worth something.

**If a company's return on capital cannot be worked out at all**, the
valuation now says so, and says what follows from it: the model assumes
the company earns exactly its cost of capital, so growth beyond the
forecast adds nothing to the value. That was happening before; it was
just not being said.
