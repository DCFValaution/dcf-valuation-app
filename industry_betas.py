"""Which Damodaran industry a company belongs to, and what its beta implies.

Two halves. The table of industry betas is generated from Damodaran's own
spreadsheet (`industry_beta_data.py`) and the ticker-to-SIC table from the
SEC (`company_sic_data.py`). The join between them - which SIC code belongs
to which industry - is the hand-written part, and it lives here because it
is a set of judgements and not data.

The SEC's SIC code is the only industry label that arrives with a company's
own filings, which is why it is the join. It is also assigned to the
REGISTRANT rather than to the business, so it is sometimes wrong about what
the company does: 3M files as a maker of surgical instruments, Amazon as a
mail-order house, Corning as a drawer of non-ferrous wire. SIC_JUDGEMENTS
records every entry where that mattered.

The rule the map follows: take the code's plain meaning, and override it
only where every filer using that code contradicts it - with the names that
forced the override written down. A map tuned to whichever companies happen
to be in the index today would be wrong the moment the index changed.
"""
from __future__ import annotations

from company_sic_data import COMPANY_SIC
from industry_beta_data import (DAMODARAN_BETAS_AS_OF, DAMODARAN_BETAS_URL,
                                INDUSTRY_BETAS, TOTAL_MARKET, IndustryBeta)

__all__ = ["COMPANY_SIC", "DAMODARAN_BETAS_AS_OF", "DAMODARAN_BETAS_URL",
           "INDUSTRY_BETAS", "TOTAL_MARKET", "IndustryBeta",
           "SIC_TO_INDUSTRY", "SIC_JUDGEMENTS", "RELEVER_TAX_RATE",
           "industry_for", "relevered_beta", "as_of_text"]

# Damodaran unlevers with a flat marginal rate, stated on the spreadsheet's
# own Inputs sheet ("If marginal tax rate, enter the marginal tax rate to
# use: 0.25"). Relevering with the same rate is the symmetric choice: using
# a company's effective rate here would relever by a different formula from
# the one the figure was unlevered by, and the difference would be read as
# business risk when it is only a mismatch of tax definitions.
RELEVER_TAX_RATE = 0.25

SIC_TO_INDUSTRY: dict[str, str] = {
    '0100': 'Farming/Agriculture',
    '1000': 'Metals & Mining',
    '1040': 'Precious Metals',
    '1311': 'Oil/Gas (Production and Exploration)',
    '1389': 'Oilfield Svcs/Equip.',
    '1400': 'Construction Supplies',
    '1520': 'Homebuilding',
    '1531': 'Homebuilding',
    '1600': 'Engineering/Construction',
    '1731': 'Engineering/Construction',
    '2000': 'Food Processing',
    '2011': 'Food Processing',
    '2015': 'Food Processing',
    '2030': 'Food Processing',
    '2033': 'Food Processing',
    '2040': 'Food Processing',
    '2060': 'Food Processing',
    '2070': 'Food Processing',
    '2080': 'Beverage (Soft)',
    '2086': 'Beverage (Soft)',
    '2090': 'Food Processing',
    '2111': 'Tobacco',
    '2300': 'Apparel',
    '2320': 'Apparel',
    '2621': 'Paper/Forest Products',
    '2650': 'Packaging & Container',
    '2670': 'Packaging & Container',
    '2711': 'Publishing & Newspapers',
    '2810': 'Chemical (Basic)',
    '2821': 'Chemical (Basic)',
    '2834': 'Drugs (Pharmaceutical)',
    '2835': 'Healthcare Products',
    '2836': 'Drugs (Biotechnology)',
    '2840': 'Household Products',
    '2842': 'Household Products',
    '2844': 'Household Products',
    '2851': 'Chemical (Specialty)',
    '2860': 'Chemical (Basic)',
    '2870': 'Chemical (Basic)',
    '2911': 'Oil/Gas (Integrated)',
    '3021': 'Shoe',
    '3100': 'Apparel',
    '3241': 'Construction Supplies',
    '3312': 'Steel',
    '3350': 'Metals & Mining',
    '3357': 'Electronics (General)',
    '3411': 'Packaging & Container',
    '3420': 'Machinery',
    '3430': 'Building Materials',
    '3480': 'Aerospace/Defense',
    '3490': 'Machinery',
    '3510': 'Machinery',
    '3523': 'Machinery',
    '3530': 'Machinery',
    '3531': 'Machinery',
    '3533': 'Oilfield Svcs/Equip.',
    '3550': 'Machinery',
    '3559': 'Semiconductor Equip',
    '3560': 'Machinery',
    '3561': 'Machinery',
    '3569': 'Machinery',
    '3570': 'Computers/Peripherals',
    '3571': 'Computers/Peripherals',
    '3572': 'Computers/Peripherals',
    '3576': 'Telecom. Equipment',
    '3577': 'Computers/Peripherals',
    '3585': 'Building Materials',
    '3590': 'Machinery',
    '3600': 'Electrical Equipment',
    '3620': 'Electrical Equipment',
    '3621': 'Electrical Equipment',
    '3630': 'Building Materials',
    '3661': 'Telecom. Equipment',
    '3663': 'Telecom. Equipment',
    '3669': 'Telecom. Equipment',
    '3670': 'Electronics (General)',
    '3672': 'Electronics (General)',
    '3674': 'Semiconductor',
    '3678': 'Electronics (General)',
    '3679': 'Electronics (General)',
    '3711': 'Auto & Truck',
    '3714': 'Auto Parts',
    '3720': 'Aerospace/Defense',
    '3721': 'Aerospace/Defense',
    '3724': 'Aerospace/Defense',
    '3728': 'Aerospace/Defense',
    '3730': 'Aerospace/Defense',
    '3743': 'Machinery',
    '3760': 'Aerospace/Defense',
    '3812': 'Aerospace/Defense',
    '3822': 'Building Materials',
    '3823': 'Electronics (General)',
    '3825': 'Electronics (General)',
    '3826': 'Healthcare Products',
    '3827': 'Electronics (General)',
    '3829': 'Electronics (General)',
    '3841': 'Healthcare Products',
    '3842': 'Healthcare Products',
    '3844': 'Healthcare Products',
    '3845': 'Healthcare Products',
    '3851': 'Healthcare Products',
    '3944': 'Recreation',
    '3990': 'Packaging & Container',
    '4011': 'Transportation (Railroads)',
    '4210': 'Trucking',
    '4213': 'Trucking',
    '4400': 'Hotel/Gaming',
    '4512': 'Air Transport',
    '4513': 'Transportation',
    '4700': 'Transportation',
    '4731': 'Transportation',
    '4812': 'Telecom (Wireless)',
    '4813': 'Telecom. Services',
    '4833': 'Broadcasting',
    '4841': 'Cable TV',
    '4899': 'Telecom. Services',
    '4911': 'Power',
    '4922': 'Oil/Gas Distribution',
    '4923': 'Oil/Gas Distribution',
    '4924': 'Oil/Gas Distribution',
    '4931': 'Utility (General)',
    '4932': 'Utility (General)',
    '4941': 'Utility (Water)',
    '4953': 'Environmental & Waste Services',
    '4991': 'Power',
    '5000': 'Retail (Distributors)',
    '5013': 'Retail (Automotive)',
    '5047': 'Healthcare Support Services',
    '5065': 'Retail (Distributors)',
    '5070': 'Retail (Building Supply)',
    '5122': 'Healthcare Support Services',
    '5140': 'Food Wholesalers',
    '5200': 'Retail (Building Supply)',
    '5211': 'Retail (Building Supply)',
    '5331': 'Retail (General)',
    '5411': 'Retail (Grocery and Food)',
    '5500': 'Retail (Automotive)',
    '5531': 'Retail (Automotive)',
    '5651': 'Retail (Special Lines)',
    '5700': 'Furn/Home Furnishings',
    '5731': 'Retail (Special Lines)',
    '5734': 'Retail (Special Lines)',
    '5810': 'Restaurant/Dining',
    '5812': 'Restaurant/Dining',
    '5912': 'Retail (Special Lines)',
    '5961': 'Retail (Special Lines)',
    '5990': 'Retail (Special Lines)',
    '6021': 'Banks (Regional)',
    '6022': 'Banks (Regional)',
    '6199': 'Financial Svcs. (Non-bank & Insurance)',
    '6200': 'Brokerage & Investment Banking',
    '6211': 'Brokerage & Investment Banking',
    '6282': 'Investments & Asset Management',
    '6311': 'Insurance (Life)',
    '6321': 'Insurance (General)',
    '6324': 'Healthcare Support Services',
    '6331': 'Insurance (Prop/Cas.)',
    '6399': 'Insurance (General)',
    '6411': 'Insurance (General)',
    '6500': 'Real Estate (General/Diversified)',
    '6510': 'R.E.I.T.',
    '6792': 'Oil/Gas (Production and Exploration)',
    '6798': 'R.E.I.T.',
    '7011': 'Hotel/Gaming',
    '7311': 'Advertising',
    '7320': 'Information Services',
    '7340': 'Business & Consumer Services',
    '7359': 'Business & Consumer Services',
    '7370': 'Software (Internet)',
    '7371': 'Computer Services',
    '7372': 'Software (System & Application)',
    '7373': 'Computer Services',
    '7374': 'Computer Services',
    '7381': 'Business & Consumer Services',
    '7389': 'Business & Consumer Services',
    '7841': 'Entertainment',
    '7900': 'Recreation',
    '7990': 'Recreation',
    '8062': 'Hospitals/Healthcare Facilities',
    '8071': 'Healthcare Support Services',
    '8090': 'Healthcare Support Services',
    '8700': 'Business & Consumer Services',
    '8731': 'Healthcare Support Services',
    '8741': 'Business & Consumer Services',
}

# Every entry where the code's plain meaning and Damodaran's taxonomy do
# not line up, or where the code is a catch-all holding several kinds of
# business. Written as: what was chosen, what was rejected, and why.
# These are the ones worth a second opinion.
SIC_JUDGEMENTS: dict[str, str] = {
    '1400':
        'Mining of non-metallic minerals reads as Metals & Mining, but the '
        'filers are aggregates producers (Martin Marietta, Vulcan) whose '
        'cycle is construction, not metal prices. Construction Supplies '
        'chosen; Metals & Mining rejected.',
    '2070':
        'Fats & oils reads as agriculture, and ADM and Bunge do trade '
        'crops, but both earn a processing margin rather than a farm yield. '
        'Food Processing chosen; Farming/Agriculture rejected.',
    '2080':
        "SIC 2080 is undifferentiated 'Beverages' and Damodaran splits soft "
        'from alcoholic. It holds Coca-Cola, PepsiCo and Keurig alongside '
        'Brown-Forman and Constellation. Beverage (Soft) chosen as the '
        'majority; the two unlevered betas are 0.58 and 0.63, so the cost '
        'of being wrong here is small.',
    '2835':
        'Diagnostic substances sit between drugs and devices. Healthcare '
        'Products chosen because the one filer (Idexx) sells instruments '
        'and consumables; Drugs (Biotechnology) rejected.',
    '2844':
        'Damodaran has no personal-care industry. Household Products chosen '
        'for cosmetics and toiletries (Colgate, Estee Lauder).',
    '2870':
        'Agricultural chemicals: CF and Mosaic are commodity nitrogen and '
        'phosphate producers, priced like basic chemicals rather than '
        'formulated ones. Chemical (Basic) chosen; Chemical (Specialty) '
        'rejected.',
    '2911':
        'Petroleum refining holds both the integrated majors (Exxon, '
        'Chevron, BP) and the pure refiners (Valero, Phillips 66, '
        'Marathon). Damodaran has no refining industry, so Oil/Gas '
        '(Integrated) was chosen as the nearest and Oil/Gas (Production '
        'and Exploration) rejected - but it averages only FOUR firms, '
        "which is a thin prior, and a pure refiner's margin behaves nothing "
        "like an integrated major's.",
    '3100':
        'Leather and leather products. Damodaran has no leather-goods '
        'industry, so Apparel was chosen for Tapestry and Retail (Special '
        'Lines) rejected: Tapestry makes what it sells.',
    '3357':
        'Drawing and insulating of non-ferrous wire reads as metals. The '
        'only filer is Corning, which makes glass and optical fibre. '
        'Electronics (General) chosen.',
    '3480':
        'Ordnance maps to Aerospace/Defense, which fits the code but not '
        'Axon, whose products are police equipment sold on subscription. '
        'Kept literal; Business & Consumer Services is the arguable '
        'alternative.',
    '3585':
        'Damodaran has no HVAC industry. Building Materials chosen for '
        'Carrier, Johnson Controls and Lennox, whose demand follows '
        'construction; Machinery rejected.',
    '3630':
        'Household appliances reads as a consumer-products code, but the '
        'filer is A. O. Smith, which makes water heaters sold through the '
        'building trade. Building Materials chosen.',
    '3730':
        'Ship and boat building maps to Shipbuilding & Marine, which '
        'averages only eight firms. The two filers are General Dynamics and '
        'Huntington Ingalls, both defence primes on government contracts, '
        'so Aerospace/Defense was chosen instead for its seventy-nine firms '
        'and its closer business model.',
    '3743':
        'Railroad equipment: Wabtec builds the rolling stock, it does not '
        'run a railway. Machinery chosen; Transportation (Railroads), which '
        'averages the operators, rejected.',
    '3822':
        'Environmental controls, which is HVAC controls. Building '
        'Materials chosen, with 3585 and for the same reason (Trane); '
        'Electrical Equipment, the literal reading of an instrument code, '
        'rejected.',
    '3823':
        "Industrial instruments is a mixed code - Danaher's life science "
        "tools, Keysight's test equipment, Roper's software. Electronics "
        '(General) chosen as the common denominator; no single industry '
        'fits all five filers.',
    '3826':
        'Laboratory analytical instruments. The filers (Agilent, Illumina, '
        'Mettler-Toledo, Revvity, Waters) sell to life-science '
        'laboratories, so Healthcare Products was chosen over the literal '
        'Electronics (General).',
    '3990':
        'Miscellaneous manufacturing. The one filer is Amcor, a packaging '
        "company, so Packaging & Container was chosen over Damodaran's own "
        'Diversified.',
    '4400':
        'Water transportation holds the three cruise lines (Carnival, Royal '
        'Caribbean, Norwegian), which sell nightly occupancy rather than '
        'freight. Hotel/Gaming chosen; Transportation and Recreation both '
        'rejected, and this one is genuinely arguable.',
    '4513':
        'Air courier (FedEx). Transportation chosen over Air Transport, '
        'which averages passenger airlines, and over Trucking, which is the '
        'ground-only half of the business.',
    '5065':
        'Wholesale electronic parts. The one filer is TE Connectivity, '
        'which manufactures connectors rather than distributing them - but '
        "the code's plain meaning is distribution and a future filer using "
        'it probably is a distributor, so this was left literal.',
    '5912':
        'Drug stores. The one filer is CVS, now mostly a health insurer and '
        'pharmacy benefit manager, which Healthcare Support Services would '
        'fit better. Left literal, because the code means retail pharmacy '
        'and the next filer to use it probably will be one.',
    '5961':
        'Catalogue and mail-order houses, which is where the SEC puts '
        'Amazon. Damodaran has no online-retail industry; Retail (Special '
        'Lines) chosen as the nearest. The other filer (CDW) is a '
        'distributor, which would point at Retail (Distributors) instead - '
        'so neither filer is well served and this is the weakest entry in '
        'the map.',
    '6021':
        'National commercial banks holds the money centres (JPMorgan, Bank '
        'of America, Citigroup, Wells Fargo) and seven regionals in the '
        'same code. Bank (Money Center) averages fifteen firms at an '
        'unlevered beta of 0.44, Banks (Regional) five hundred and '
        'sixty-'
        'eight at 0.37. Banks (Regional) chosen: it is the majority of the '
        "filers, and the money centres' own betas are measured precisely "
        'enough that they never reach this map.',
    '6199':
        'Finance services holds American Express, Synchrony and Coinbase - '
        'a card network, a store-card lender and a crypto exchange. '
        'Financial Svcs. (Non-bank & Insurance) is the literal match, and '
        'its D/E of 2.72 relevers badly onto a firm with no debt. It was '
        'chosen anyway and Brokerage & Investment Banking rejected, '
        "because Coinbase's own beta is measured precisely and so never "
        'reaches this map, while American Express and Synchrony are '
        'lenders rather than brokers.',
    '6324':
        'Hospital and medical service plans. The filers are the '
        'managed-'
        'care companies (UnitedHealth, Elevance, Cigna, Humana, Centene), '
        'which Damodaran classifies as Healthcare Support Services, so '
        'that was chosen and Insurance (General) rejected despite the '
        "code's wording.",
    '6411':
        'Insurance agents and brokers earn fees and underwrite nothing, so '
        'Business & Consumer Services is arguable. Insurance (General) '
        'chosen because that is where Damodaran himself puts the brokers.',
    '6792':
        'Oil royalty traders. Texas Pacific Land owns acreage and produces '
        "nothing, so its cost structure is unlike an explorer's, but "
        'Damodaran has no royalty industry, so Oil/Gas (Production and '
        'Exploration) was chosen as the nearest. Investments & Asset '
        'Management is the arguable alternative, a royalty book being '
        'closer to a portfolio than to a drilling programme.',
    '7320':
        'Consumer credit reporting, which in the index means the rating and '
        "data agencies (Equifax, Moody's, S&P Global). Information Services "
        'chosen, and it averages only fifteen firms.',
    '7359':
        'Equipment rental (United Rentals). Business & Consumer Services '
        'chosen; Machinery, which averages the manufacturers, rejected.',
    '7370':
        'Computer programming and data processing is where the SEC puts '
        'Alphabet and Meta, and also FactSet and AppLovin. Software '
        '(Internet) chosen, whose unlevered beta of 1.59 is the highest in '
        'the table - too high for FactSet, about right for the other two.',
    '7374':
        'Computer processing and data preparation holds ADP, Workday, '
        'Verisk and Reddit - payroll, SaaS, analytics and a social network. '
        'Computer Services chosen; no industry fits all four.',
    '7389':
        'Business services NEC is the largest catch-all in the map - '
        'seventeen filers including Visa, Mastercard, PayPal, Accenture, '
        'Uber, DoorDash, eBay and FIS. Business & Consumer Services chosen '
        'as the only industry broad enough to be defensible, which is also '
        'to say it is not a close fit for any of them.',
    '8731':
        'Commercial physical and biological research holds both the '
        'contract research organisations (Charles River, IQVIA) and a '
        'biotech (Incyte). Healthcare Support Services chosen as the '
        'majority; Drugs (Biotechnology) rejected.',
}


def as_of_text() -> str:
    """The industry table's date, written the way a sentence wants it."""
    return f"{DAMODARAN_BETAS_AS_OF:%B %Y}"


def relevered_beta(industry: IndustryBeta, debt_to_equity: float) -> float:
    """The industry's unlevered beta, relevered on *debt_to_equity*.

        beta_levered = beta_unlevered x (1 + (1 - t) x D/E)

    *debt_to_equity* is the company's own, at market value of equity and
    book value of debt - the same asymmetry the WACC weights use, and for
    the same reason. A negative or absurd ratio is treated as zero rather
    than propagated: the industry's own average structure is a better
    stand-in than a number that cannot be right.
    """
    ratio = debt_to_equity if debt_to_equity and debt_to_equity > 0 else 0.0
    if ratio > MAX_DEBT_TO_EQUITY:
        ratio = MAX_DEBT_TO_EQUITY
    return industry.unlevered_beta * (1.0 + (1.0 - RELEVER_TAX_RATE) * ratio)


# A leverage ratio above this is almost always a company whose equity has
# nearly vanished, where relevering amplifies the industry beta into
# nonsense. Clamping is visible in the provenance rather than silent.
MAX_DEBT_TO_EQUITY = 3.0


def industry_capex_for(ticker: str):
    """Damodaran's investment intensities for *ticker*'s industry.

    The same SIC map as `industry_for`, because they are the same
    industries - the two tables are different columns of the same annual
    exercise. Returns None where the company's industry is unknown, so the
    caller can say so rather than quietly using the whole market's.
    """
    from industry_capex_data import INDUSTRY_CAPEX

    code = COMPANY_SIC.get((ticker or "").upper().strip())
    if code:
        name = SIC_TO_INDUSTRY.get(code)
        if name:
            return INDUSTRY_CAPEX.get(name)
    return None


def industry_for(ticker: str) -> tuple[IndustryBeta, str | None, bool]:
    """The industry beta to use for *ticker*.

    Returns (industry, SIC code, mapped). `mapped` is False when the ticker
    has no SIC code on file or its code is not in the map, in which case the
    industry is the whole market - which is an honest "this company's
    industry is unknown" rather than a guess at one.
    """
    code = COMPANY_SIC.get((ticker or "").upper().strip())
    if code:
        name = SIC_TO_INDUSTRY.get(code)
        industry = INDUSTRY_BETAS.get(name) if name else None
        if industry is not None:
            return industry, code, True
    return INDUSTRY_BETAS[TOTAL_MARKET], code or None, False
