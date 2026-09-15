# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Domain classifier for AS-V7 adaptive persona routing.

Classifies questions as single-domain or multi-domain to determine whether
to use diverse personas (multi-domain) or neutral expert (single-domain).

See: /opt/agentstorming/driver/decisions/20260517T190630-iter140-design-as-v7-adaptive.md
"""

from __future__ import annotations


# Domain keywords for classification
DOMAIN_KEYWORDS = {
    'physics': [
        'force', 'energy', 'mass', 'velocity', 'momentum', 'acceleration',
        'quantum', 'relativity', 'newton', 'mechanics', 'thermodynamics',
        'electromagnetic', 'wave', 'particle', 'field', 'gravity', 'photon',
        'electron', 'nuclear', 'atom', 'radiation', 'joule', 'watt', 'volt',
        'magnetic', 'electric', 'circuit', 'resistor', 'capacitor', 'induction'
    ],
    'chemistry': [
        'molecule', 'atom', 'reaction', 'bond', 'element', 'compound',
        'acid', 'base', 'periodic', 'ion', 'electron', 'oxidation',
        'reduction', 'catalyst', 'equilibrium', 'ph', 'solvent', 'solution',
        'organic', 'inorganic', 'polymer', 'synthesis', 'titration',
        'molar', 'molarity', 'concentration', 'reactant', 'product'
    ],
    'biology': [
        'cell', 'organism', 'gene', 'protein', 'evolution', 'species',
        'dna', 'rna', 'enzyme', 'membrane', 'nucleus', 'mitochondria',
        'chloroplast', 'photosynthesis', 'respiration', 'tissue', 'organ',
        'ecosystem', 'metabolism', 'chromosome', 'mutation', 'natural selection',
        'phenotype', 'genotype', 'allele', 'heredity', 'taxonomy'
    ],
    'mathematics': [
        'equation', 'derivative', 'integral', 'matrix', 'vector', 'theorem',
        'proof', 'function', 'limit', 'polynomial', 'trigonometry',
        'calculus', 'algebra', 'geometry', 'probability', 'statistics',
        'logarithm', 'exponential', 'differential', 'linear', 'quadratic',
        'prime', 'factorial', 'permutation', 'combination', 'set theory'
    ],
    'computer_science': [
        'algorithm', 'complexity', 'data structure', 'recursion', 'sorting',
        'graph', 'tree', 'binary', 'hash', 'array', 'linked list',
        'stack', 'queue', 'big o', 'polynomial time', 'np-complete',
        'programming', 'compiler', 'operating system', 'database',
        'network', 'protocol', 'encryption', 'cache', 'memory'
    ],
    'history': [
        'war', 'treaty', 'empire', 'revolution', 'dynasty', 'century',
        'ancient', 'medieval', 'renaissance', 'colonial', 'independence',
        'monarchy', 'republic', 'democracy', 'feudal', 'civilization',
        'conquest', 'battle', 'alliance', 'declaration', 'constitution'
    ],
    'law': [
        'statute', 'precedent', 'court', 'jurisdiction', 'plaintiff',
        'defendant', 'contract', 'tort', 'liability', 'damages',
        'verdict', 'appeal', 'constitutional', 'criminal', 'civil',
        'federal', 'supreme court', 'testimony', 'evidence', 'jury'
    ],
    'economics': [
        'market', 'demand', 'supply', 'price', 'utility', 'inflation',
        'gdp', 'fiscal', 'monetary', 'interest rate', 'unemployment',
        'recession', 'growth', 'trade', 'export', 'import', 'tariff',
        'consumer', 'producer', 'equilibrium', 'elasticity', 'monopoly'
    ],
    'psychology': [
        'behavior', 'cognitive', 'perception', 'learning', 'memory',
        'emotion', 'personality', 'motivation', 'intelligence', 'consciousness',
        'neural', 'brain', 'stimulus', 'response', 'conditioning',
        'reinforcement', 'mental', 'disorder', 'therapy', 'development'
    ],
    'medicine': [
        'disease', 'treatment', 'diagnosis', 'symptom', 'patient',
        'clinical', 'therapy', 'drug', 'vaccine', 'infection',
        'immune', 'virus', 'bacteria', 'pathogen', 'disorder',
        'syndrome', 'surgery', 'hospital', 'physician', 'prescription'
    ],
    'linguistics': [
        'language', 'grammar', 'syntax', 'semantics', 'phonetics',
        'morphology', 'vocabulary', 'dialect', 'translation', 'meaning',
        'word', 'sentence', 'phrase', 'verb', 'noun', 'adjective'
    ],
    'philosophy': [
        'ethics', 'metaphysics', 'epistemology', 'logic', 'truth',
        'knowledge', 'belief', 'reason', 'argument', 'fallacy',
        'moral', 'right', 'wrong', 'justice', 'virtue', 'consciousness'
    ],
}


def classify_question_domain(question_text: str, min_domains: int = 2) -> str:
    """Classify if question spans multiple academic domains.

    Args:
        question_text: The question text to classify
        min_domains: Minimum number of domains to classify as multi_domain (default: 2)

    Returns:
        'multi_domain' if ≥min_domains detected, else 'single_domain'
    """
    question_lower = question_text.lower()
    domains_detected = set()

    for domain, keywords in DOMAIN_KEYWORDS.items():
        # Check if any keyword from this domain appears in the question
        if any(kw in question_lower for kw in keywords):
            domains_detected.add(domain)

    # If ≥min_domains detected, classify as multi-domain
    if len(domains_detected) >= min_domains:
        return 'multi_domain'
    else:
        return 'single_domain'


def get_detected_domains(question_text: str) -> set[str]:
    """Get the set of domains detected in the question.

    Useful for debugging and analysis.
    """
    question_lower = question_text.lower()
    domains_detected = set()

    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in question_lower for kw in keywords):
            domains_detected.add(domain)

    return domains_detected
