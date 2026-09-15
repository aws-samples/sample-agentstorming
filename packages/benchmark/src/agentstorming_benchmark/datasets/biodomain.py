# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""BioDomain: Biology → Computer Science Cross-Domain Synthesis Benchmark

Tests ability to transfer biological mechanisms to solve computer science problems.
Each problem requires identifying an analogous biological mechanism and applying it
to a CS context.

Designed to test whether Agent Storming's advantage on cross-domain synthesis
generalizes beyond CS↔CS transfers (XDomain) to Natural Science→Engineering transfers.

Key discriminative features (same as XDomain):
1. Explicit cross-domain mapping requirement (biology → CS)
2. Open-ended generation (not MCQ, graded by LLM-as-judge)
3. Maximally distant domains (biology ≠ computer science)
4. Multiple valid approaches (rubric scores partial credit)
5. Synthesis-level reasoning (Bloom's highest: create, evaluate, synthesize)
"""

from __future__ import annotations
from typing import TypedDict

from ..types import Question
from . import register


def format_open_ended_prompt(q: Question) -> str:
    """Format prompt for open-ended questions (no multiple choice).

    Used for BioDomain benchmarks which are graded by LLM-as-judge on the full
    synthesis text, not extracted answer letter.
    """
    body = q.question.strip()
    return f"""Question ({q.subject}):
{body}

Provide a detailed answer with technical depth. Think step-by-step if helpful.
Keep your response ≤500 words."""


class BioDomainProblem(TypedDict):
    """Raw problem before conversion to Question."""
    id: str
    bio_domain: str  # Source biological domain
    cs_domain: str   # Target CS domain
    problem: str
    hint: str
    gold_answer: str
    rubric: list[str]  # Evaluation criteria (each worth 1 point)
    max_score: int


# 30 biology → computer science cross-domain synthesis problems
PROBLEMS: list[BioDomainProblem] = [
    # Evolution/Natural Selection → Various CS Domains
    {
        "id": "bio_001_dna_otp_storage",
        "bio_domain": "Molecular Biology",
        "cs_domain": "Cryptography",
        "problem": "You need to store one-time pads for perfect encryption. Each pad is 1TB of random data that must be used exactly once. How can you ensure a pad is never reused, even after power loss or hardware replacement?",
        "hint": "Consider how DNA stores information with built-in error detection.",
        "gold_answer": "Apply DNA base-pairing from molecular biology. Store the OTP as complementary pairs (A-T, G-C). When using a position, 'denature' it by XORing with a fixed pattern. Verification: before use, check that position is still paired (not denatured). After use, unpaired positions can never re-pair. This gives physical write-once guarantee. Storage: 4 bases = 2 bits, so 1TB → 4TB of 'DNA' storage.",
        "rubric": [
            "Identifies DNA complementary base pairing or similar error-detection mechanism",
            "Applies to write-once guarantee (used pads become unpaired)",
            "Explains verification (check pairing status before use)",
            "Provides concrete mechanism (XOR, base encoding)",
            "Addresses tradeoffs (2× storage overhead, irreversibility)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_002_immune_negative_selection",
        "bio_domain": "Immunology",
        "cs_domain": "Security",
        "problem": "Your firewall needs to detect never-seen-before attacks (zero-days) without a training set of malicious traffic. Supervised ML won't work because you don't have labeled attack data. How do you build an anomaly detector?",
        "hint": "Think about how the immune system distinguishes 'self' from 'non-self' without prior exposure to every pathogen.",
        "gold_answer": "Apply negative selection from immunology. The immune system generates random T-cell receptors, then DELETES any that bind to 'self' proteins (thymic selection). For network traffic: (1) Collect normal traffic (self), (2) Generate random detectors, (3) Delete detectors that match normal traffic, (4) Remaining detectors flag anomalies. This is the Artificial Immune System (AIS) approach.",
        "rubric": [
            "Identifies negative selection or clonal deletion from immunology",
            "Applies to anomaly detection (train on normal, not abnormal)",
            "Explains detector generation and self-matching deletion",
            "Provides concrete mechanism (detectors, matching function)",
            "Addresses limitations (coverage, false positives from incomplete self-set)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_003_quorum_sensing_consensus",
        "bio_domain": "Microbiology",
        "cs_domain": "Distributed Systems",
        "problem": "You have 1000 IoT sensors in a wireless mesh. They need to agree on when to go into low-power mode, but there's no central coordinator and messages are unreliable. How do sensors reach consensus without expensive protocols like Paxos?",
        "hint": "Consider how bacteria coordinate behavior in large colonies without leaders.",
        "gold_answer": "Apply quorum sensing from microbiology. Bacteria release signaling molecules; when concentration exceeds a threshold, the colony switches behavior. For IoT: (1) Each sensor periodically broadcasts a 'vote' message, (2) Each sensor counts votes received in the last T seconds, (3) When count > threshold, switch mode. This is leaderless, fault-tolerant, and self-stabilizing. Trade-off: convergence time vs message overhead.",
        "rubric": [
            "Identifies quorum sensing or autocrine signaling from microbiology",
            "Applies to distributed consensus (vote counting, threshold-based decision)",
            "Explains leaderless coordination mechanism",
            "Provides concrete parameters (broadcast period, vote window, threshold)",
            "Addresses tradeoffs (convergence speed, message overhead, network partition handling)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_004_protein_misfolding_collision",
        "bio_domain": "Cell Biology",
        "cs_domain": "Databases",
        "problem": "Your database uses a hash table for indexing. Hash collisions are rare but catastrophic—they corrupt data silently. You need to detect collisions BEFORE they corrupt anything. Standard approaches check every insert (expensive). Can you do better?",
        "hint": "Cells have chaperone proteins that detect misfolded proteins before they aggregate.",
        "gold_answer": "Apply protein folding chaperones (like HSP70). Chaperones don't prevent misfolding; they DETECT it by recognizing exposed hydrophobic residues. For hashing: use a second, independent hash function as a 'chaperone.' On insert, compute both h1(key) and h2(key). Store both. On lookup, verify both match. If h1 collides but h2 doesn't, flag error. Cost: 2× hash computation + 1 extra int per entry.",
        "rubric": [
            "Identifies chaperone proteins or misfolding detection from cell biology",
            "Applies to collision detection (secondary verification mechanism)",
            "Explains independent verification (second hash function)",
            "Provides concrete mechanism (dual hashing, comparison on lookup)",
            "Addresses tradeoffs (storage overhead, false positive rate)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_005_camouflage_steganography",
        "bio_domain": "Ecology",
        "cs_domain": "Security",
        "problem": "You need to exfiltrate data from a network with deep packet inspection (DPI) that flags encrypted traffic. Encryption would be detected. How do you hide data in plain sight?",
        "hint": "Consider how prey animals avoid detection by predators.",
        "gold_answer": "Apply camouflage/mimicry from ecology. Animals blend into background or mimic harmless species. For network: encode data into innocuous-looking HTTP traffic (e.g., embed in image metadata, HTML comments, timing patterns). The payload 'looks like' normal browsing to DPI. This is network steganography. Detection: statistical tests can spot unnatural patterns (steganalysis).",
        "rubric": [
            "Identifies camouflage or mimicry from ecology/zoology",
            "Applies to data hiding (mimic normal traffic patterns)",
            "Explains embedding mechanism (covert channel within overt protocol)",
            "Provides concrete method (image metadata, timing, protocol field abuse)",
            "Addresses detectability (steganalysis, statistical tests)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_006_red_queen_adversarial_ml",
        "bio_domain": "Evolutionary Biology",
        "cs_domain": "Machine Learning",
        "problem": "You've trained a fraud detection model. It works great initially, but fraudsters quickly adapt and your model's accuracy drops. Retraining on new fraud helps temporarily, but then they adapt again. This is an arms race. How do you stay ahead?",
        "hint": "In evolution, predators and prey co-evolve—neither ever 'wins.'",
        "gold_answer": "Apply the Red Queen Hypothesis from evolutionary biology ('running to stay in place'). Instead of static training, use adversarial co-evolution: (1) Train detector D, (2) Train generator G to fool D (like GANs), (3) Retrain D on G's outputs, (4) Repeat. Detector stays robust because it's always trained against the hardest adversary. This is adversarial training / robust ML.",
        "rubric": [
            "Identifies Red Queen Hypothesis or evolutionary arms race",
            "Applies to model robustness (co-evolution with adversary)",
            "Explains iterative adversarial training process",
            "Provides concrete method (GAN-style training loop)",
            "Addresses convergence or stopping criteria",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_007_neural_timing_covert_channel",
        "bio_domain": "Neuroscience",
        "cs_domain": "Security",
        "problem": "Two processes on a shared server need to communicate covertly (the OS forbids direct IPC). They can only observe CPU timing. How do they send bits?",
        "hint": "Neurons encode information in the precise timing of spikes, not just spike rate.",
        "gold_answer": "Apply spike-timing-dependent encoding from neuroscience. Process A modulates CPU usage to create timing patterns (e.g., spike at time t for bit=1, no spike for bit=0). Process B measures scheduling latency to detect spikes. This is a timing covert channel. Detection: statistical tests can spot non-natural CPU patterns.",
        "rubric": [
            "Identifies spike timing or temporal coding from neuroscience",
            "Applies to covert channel (encode info in timing, not content)",
            "Explains sender modulation and receiver detection",
            "Provides concrete mechanism (CPU contention, scheduling latency)",
            "Addresses detectability (timing anomaly detection)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_008_hgt_capability_sharing",
        "bio_domain": "Microbiology",
        "cs_domain": "Software Engineering",
        "problem": "You're designing a microservices architecture. Services need to share capabilities (permissions) dynamically. Traditional access control lists (ACLs) are rigid and centralized. How do you enable decentralized capability sharing?",
        "hint": "Bacteria can share genetic material directly without reproducing.",
        "gold_answer": "Apply horizontal gene transfer (HGT) from microbiology. Bacteria transfer plasmids (mobile genetic elements) to neighbors. For microservices: use cryptographic capability tokens (bearer tokens) that can be passed service-to-service. Token grants permission; possession = authority. No central ACL. This is object-capability security. Risk: token theft (like plasmid theft), requires secure channels.",
        "rubric": [
            "Identifies horizontal gene transfer or plasmid sharing",
            "Applies to decentralized capability transfer",
            "Explains bearer token model (possession = permission)",
            "Provides concrete mechanism (cryptographic tokens, passing protocol)",
            "Addresses security risks (token theft, revocation challenges)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_009_circadian_access_control",
        "bio_domain": "Chronobiology",
        "cs_domain": "Security",
        "problem": "Insider threats often occur outside business hours (3am data exfiltration). You want access control that restricts sensitive operations to normal working hours, but calendars and timezones vary by user. How do you implement this flexibly?",
        "hint": "Biological organisms have internal clocks that adapt to environmental cues.",
        "gold_answer": "Apply circadian rhythm from chronobiology. Each user has a personal 'access rhythm' learned from their historical login patterns. Operations outside their normal rhythm trigger additional verification (MFA, manager approval). The rhythm adapts over time (like jet lag recovery). This is behavioral biometrics with time-of-day features.",
        "rubric": [
            "Identifies circadian rhythm or biological clock mechanism",
            "Applies to access control (user-specific temporal patterns)",
            "Explains adaptive learning from historical behavior",
            "Provides concrete mechanism (time-of-day features, anomaly scoring)",
            "Addresses adaptation (rhythm shift for travel, lifestyle changes)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_010_swarm_ddos_detection",
        "bio_domain": "Entomology",
        "cs_domain": "Security",
        "problem": "Your CDN detects DDoS attacks by monitoring request rates. But attackers now use 'slow and low' attacks (many bots, each sending few requests). Per-IP rate limiting doesn't catch this. How do you detect coordinated swarms?",
        "hint": "Ant colonies find food via pheromone trails—no single ant knows the full path.",
        "gold_answer": "Apply swarm intelligence from entomology. Ants leave pheromone trails; paths to food get reinforced, dead ends fade. For DDoS detection: track request graph (IP → URL). Coordinated bots create unusual graph patterns (many IPs converge on same resources with similar timing). Detect by graph metrics (clustering coefficient, betweenness centrality). Individual bots look normal; swarm structure is anomalous.",
        "rubric": [
            "Identifies swarm intelligence or emergent collective behavior",
            "Applies to attack detection (graph-level patterns vs per-node)",
            "Explains collective signature (graph structure, temporal correlation)",
            "Provides concrete mechanism (graph metrics, clustering detection)",
            "Addresses false positives (legitimate flash crowds, CDN caching)",
        ],
        "max_score": 5,
    },

    # Additional problems 11-30 (stubs to be filled)
    {
        "id": "bio_011_fitness_landscape_hyperparams",
        "bio_domain": "Evolutionary Biology",
        "cs_domain": "Machine Learning",
        "problem": "You're tuning hyperparameters for a deep neural network with 50+ hyperparameters. Grid search is infeasible. Random search finds local optima but misses better regions. How do you explore the hyperparameter space efficiently?",
        "hint": "Evolution optimizes complex fitness landscapes without knowing the gradient.",
        "gold_answer": "Apply fitness landscape exploration from evolutionary biology. Use population-based optimization: maintain multiple hyperparameter configs, evaluate fitness (validation accuracy), select top performers, generate new configs via crossover (blend hyperparams) and mutation (random perturbation). This balances exploration (mutation, diversity) and exploitation (selection). Modern approach: population-based training (PBT).",
        "rubric": [
            "Identifies fitness landscape or evolutionary optimization",
            "Applies to hyperparameter optimization (population-based search)",
            "Explains selection, crossover/recombination, mutation",
            "Addresses exploration-exploitation tradeoff",
            "Mentions modern implementation (evolutionary strategies, PBT)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_012_sexual_selection_features",
        "bio_domain": "Evolutionary Biology",
        "cs_domain": "Machine Learning",
        "problem": "You're doing feature engineering for fraud detection. You have 1000+ raw features. Many are cheap to compute but low-signal. Some are expensive (query external APIs) but high-signal. How do you decide which expensive features are worth computing?",
        "hint": "Peacocks grow costly tail feathers—only healthy birds can afford them.",
        "gold_answer": "Apply costly signaling from sexual selection. Expensive features that are hard to fake (like API calls to verify identity) are more informative than cheap features (like user-agent strings) because fraudsters can't easily produce them. Use cost-benefit analysis: only compute expensive features when prior probability crosses a threshold. This is the economics of costly signals applied to feature selection.",
        "rubric": [
            "Identifies costly signaling or handicap principle from biology",
            "Applies to feature selection (expensive features as strong signals)",
            "Explains cost-benefit tradeoff (compute only when worth it)",
            "Provides concrete mechanism (threshold-based feature computation)",
            "Addresses false positives/negatives from selective feature use",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_013_genetic_drift_ensembles",
        "bio_domain": "Population Genetics",
        "cs_domain": "Machine Learning",
        "problem": "You train an ensemble of neural networks. If all models make the same mistakes, the ensemble doesn't help. You want diversity. Simply using different random seeds gives some diversity, but not much. How do you ensure models are truly diverse?",
        "hint": "In small populations, random sampling causes genetic drift even without selection pressure.",
        "gold_answer": "Apply genetic drift from population genetics. Train each model on a random bootstrap sample of the data (sampling with replacement). This creates 'founder effects'—each model sees a slightly different data distribution. Additionally, use different architectures, loss functions, or data augmentations. This is the basis of bagging and random forests. Diversity comes from random sampling, not just initialization.",
        "rubric": [
            "Identifies genetic drift or founder effect from population genetics",
            "Applies to ensemble diversity (different data samples per model)",
            "Explains bootstrap sampling or data partitioning",
            "Provides concrete mechanism (bagging, random subspace method)",
            "Addresses bias-variance tradeoff in ensembles",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_014_convergent_evolution_transfer",
        "bio_domain": "Evolutionary Biology",
        "cs_domain": "Machine Learning",
        "problem": "You have a pretrained image classifier trained on natural images (ImageNet). You want to adapt it for medical imaging (X-rays). Transfer learning usually works, but medical images look very different from natural images. How do you leverage the pretrained model effectively?",
        "hint": "Different species independently evolve similar solutions (wings in birds/bats/insects).",
        "gold_answer": "Apply convergent evolution: different starting points reach similar solutions for similar problems. Instead of fine-tuning all layers, freeze early layers (low-level features like edges are universal) and train only high-level layers on medical data. Alternatively, use domain adaptation: align feature distributions between natural and medical images. The pretrained model provides 'evolutionary prior' even for distant domains.",
        "rubric": [
            "Identifies convergent evolution or analogous structures",
            "Applies to transfer learning (leverage pretrained features)",
            "Explains layer-wise freezing or domain adaptation",
            "Addresses feature hierarchy (low-level universal, high-level task-specific)",
            "Mentions tradeoffs (how much to freeze vs retrain)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_015_clonal_expansion_cache_warming",
        "bio_domain": "Immunology",
        "cs_domain": "Databases",
        "problem": "Your database has a cache. After a cold start (empty cache), performance is terrible for the first hour until popular queries fill the cache. You want to 'warm' the cache proactively. How do you predict what to cache before queries arrive?",
        "hint": "When the immune system encounters a pathogen, it rapidly clones successful antibodies.",
        "gold_answer": "Apply clonal expansion from immunology. After detecting a 'hit' (query), rapidly replicate related entries. Use query logs to identify popular patterns, then proactively load similar queries into cache (cache warming). When a query is popular, clone/prefetch related queries (same table, nearby IDs). This is proactive caching based on observed 'threats' (workload patterns).",
        "rubric": [
            "Identifies clonal expansion or clonal selection from immunology",
            "Applies to cache warming (replicate popular/related entries)",
            "Explains pattern detection from query logs",
            "Provides concrete mechanism (prefetching, related query identification)",
            "Addresses tradeoffs (cache pollution, eviction policy)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_016_antibody_diversity_fuzzy_match",
        "bio_domain": "Immunology",
        "cs_domain": "Information Retrieval",
        "problem": "Users search your e-commerce site with typos, misspellings, and synonyms ('phone' vs 'mobile'). Exact string matching returns no results. Edit distance helps but is slow for millions of products. How do you handle fuzzy matching efficiently?",
        "hint": "The immune system generates billions of antibody variants to match unknown pathogens.",
        "gold_answer": "Apply antibody diversity generation (V(D)J recombination). Generate multiple representations of each product (synonyms, phonetic encodings, n-grams) at index time. Store in a trie or BK-tree for fast approximate matching. At query time, generate variants of the search term and check against precomputed variants. This is combinatorial expansion at index time for fast lookup, inspired by immune repertoire diversity.",
        "rubric": [
            "Identifies antibody diversity or V(D)J recombination",
            "Applies to fuzzy matching (precompute variants)",
            "Explains index-time expansion vs query-time search",
            "Provides concrete data structure (trie, BK-tree, n-gram index)",
            "Addresses tradeoffs (index size vs query speed)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_017_autoimmune_false_positive",
        "bio_domain": "Immunology",
        "cs_domain": "Security",
        "problem": "Your intrusion detection system has high false positive rate—it flags legitimate admin actions as attacks. Tuning thresholds helps but hurts true positive rate. The system needs to 'learn' what normal admin behavior looks like. How?",
        "hint": "Autoimmune diseases occur when the immune system attacks the body's own cells.",
        "gold_answer": "Apply self-tolerance mechanisms from immunology. The immune system uses central tolerance (delete self-reactive cells) and peripheral tolerance (suppress self-reactive responses). For IDS: build a whitelist of 'self' (normal admin patterns), suppress alerts for self-actions. Continuously update self-model as admin behavior evolves. If a flagged action is confirmed benign, add to self-set. This reduces autoimmune-like false positives.",
        "rubric": [
            "Identifies autoimmune disease or self-tolerance mechanism",
            "Applies to false positive reduction (distinguish self from non-self)",
            "Explains whitelist/self-model construction",
            "Provides update mechanism (adaptive self-definition)",
            "Addresses edge cases (evolving legitimate behavior, mimicry attacks)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_018_memory_cells_adaptive_cache",
        "bio_domain": "Immunology",
        "cs_domain": "Databases",
        "problem": "Your web app has seasonal traffic patterns (holiday shopping, tax season). Standard LRU cache evicts entries as soon as they're not used, so seasonal data is evicted and must be reloaded next season. How do you retain seasonal data cheaply?",
        "hint": "The immune system remembers past infections for decades via memory cells.",
        "gold_answer": "Apply immunological memory. Keep a small 'memory tier' of cache for entries that were popular in past seasons. When evicting from hot cache, check if entry had historical importance (popular last December). If yes, move to memory tier instead of deleting. Memory tier has longer TTL. On seasonal pattern detection, promote memory entries back to hot cache. This is adaptive caching with long-term memory.",
        "rubric": [
            "Identifies memory cells or immunological memory",
            "Applies to cache retention (long-term memory tier)",
            "Explains tiered cache architecture (hot vs memory)",
            "Provides concrete eviction policy (historical importance)",
            "Addresses tradeoffs (memory overhead, staleness)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_019_keystone_species_critical_infra",
        "bio_domain": "Ecology",
        "cs_domain": "Distributed Systems",
        "problem": "Your microservices architecture has 200 services. When one service fails, the impact varies—some cause cascading failures, others barely matter. You need to identify which services are most critical for system stability. How?",
        "hint": "Removing a keystone species (like sea otters) collapses the entire ecosystem.",
        "gold_answer": "Apply keystone species concept from ecology. Build a service dependency graph. Compute centrality metrics (betweenness, PageRank). Services with high betweenness are 'keystones'—removing them disconnects the graph. Experimentally, use chaos engineering: disable each service and measure cascade size. Keystone services have disproportionate impact. Invest in redundancy/resilience for keystones.",
        "rubric": [
            "Identifies keystone species or disproportionate impact",
            "Applies to criticality analysis (identify high-impact services)",
            "Explains graph-based metrics (centrality, reachability)",
            "Provides experimental validation method (chaos engineering)",
            "Addresses mitigation (redundancy, circuit breakers for keystones)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_020_trophic_cascade_dependency",
        "bio_domain": "Ecology",
        "cs_domain": "Software Engineering",
        "problem": "You update a low-level library (logging utility). Surprisingly, this breaks frontend UI tests—there's no direct dependency. The break propagated through 5 intermediate layers. How do you predict such multi-hop failures before deploying?",
        "hint": "In ecosystems, removing top predators affects plants through intermediate herbivores (trophic cascade).",
        "gold_answer": "Apply trophic cascade from ecology. Changes propagate through dependency chains with non-obvious effects. Build a full dependency graph (transitive closure). Use impact analysis: when changing a library, trace all paths from that node to end-user features. Run integration tests for all reachable features, not just direct dependents. This is multi-hop dependency tracking inspired by trophic levels.",
        "rubric": [
            "Identifies trophic cascade or multi-hop effects",
            "Applies to dependency analysis (transitive impact)",
            "Explains dependency graph traversal (transitive closure)",
            "Provides concrete testing strategy (test all reachable features)",
            "Addresses tradeoffs (test coverage vs test time)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_021_invasive_species_malware",
        "bio_domain": "Ecology",
        "cs_domain": "Security",
        "problem": "A new malware strain spreads rapidly through your network. It exploits a zero-day, so signatures don't work. You need to understand its spread pattern to contain it. Traditional network monitoring shows it jumping unpredictably. How do you model its propagation?",
        "hint": "Invasive species like zebra mussels spread through interconnected waterways.",
        "gold_answer": "Apply invasive species ecology. Model network as a habitat with heterogeneous connectivity. Malware spreads via epidemic models (SIR: Susceptible-Infected-Recovered). Identify super-spreaders (highly connected hosts). Use network segmentation as 'quarantine.' Track R0 (reproduction number) to predict spread. Containment: isolate infected hosts, patch susceptible hosts, reduce network connectivity (like ecological corridors).",
        "rubric": [
            "Identifies invasive species or epidemic spread models",
            "Applies to malware propagation (SIR or similar model)",
            "Explains network topology effects (super-spreaders, hubs)",
            "Provides containment strategy (segmentation, quarantine)",
            "Addresses measurement (R0, spread rate, vulnerable population)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_022_succession_recovery",
        "bio_domain": "Ecology",
        "cs_domain": "Distributed Systems",
        "problem": "Your distributed database cluster experiences a data center failure (power outage). You need to restore service. You could restore from backup (slow, stale data) or rebuild from replicas (fast, fresh data). What's the optimal recovery strategy?",
        "hint": "After a forest fire, ecosystems recover through ecological succession—pioneer species first, climax community later.",
        "gold_answer": "Apply ecological succession. Use staged recovery: (1) Primary succession: bring up minimal service with stale backup (fast, gets users online), (2) Secondary succession: backfill fresh data from replicas in background, (3) Climax state: full replication restored. This prioritizes availability (fast partial recovery) over consistency (slow full recovery). Users see degraded service quickly rather than waiting for full restore.",
        "rubric": [
            "Identifies ecological succession or staged recovery",
            "Applies to system recovery (phased restore strategy)",
            "Explains stages (fast minimal → full restoration)",
            "Addresses availability vs consistency tradeoff",
            "Provides concrete mechanism (partial restore, background sync)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_023_mitochondria_energy",
        "bio_domain": "Cell Biology",
        "cs_domain": "Distributed Systems",
        "problem": "Your data center has variable power costs (cheap at night, expensive at peak). You want to shift compute-heavy batch jobs to cheap hours. But jobs need results by morning. How do you manage this without a central scheduler (each server decides independently)?",
        "hint": "Mitochondria generate ATP locally in each cell rather than receiving it from a central source.",
        "gold_answer": "Apply local energy management from mitochondria. Each server maintains local power cost awareness and job queue. Servers autonomously delay non-urgent jobs when power is expensive, execute during cheap periods. Use local decision rules (threshold: if cost < $X/kWh, run batch jobs). This is decentralized energy optimization, like cells managing ATP production locally.",
        "rubric": [
            "Identifies mitochondria or local power generation",
            "Applies to decentralized resource management",
            "Explains local decision rules (cost-based scheduling)",
            "Addresses coordination (how servers align without central control)",
            "Mentions tradeoffs (local vs global optimality)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_024_membrane_firewall",
        "bio_domain": "Cell Biology",
        "cs_domain": "Security",
        "problem": "Your network firewall blocks/allows traffic based on rules (IP, port, protocol). But attackers tunnel through allowed protocols (HTTP) to bypass rules. You need a firewall that inspects content, not just headers. How do you implement selective permeability?",
        "hint": "Cell membranes allow some molecules through while blocking others via selective channels.",
        "gold_answer": "Apply selective membrane permeability. Cell membranes use ion channels that open only for specific molecules. For firewalls: implement deep packet inspection (DPI) with protocol-specific analyzers. HTTP traffic is inspected by HTTP-aware filter (checks headers, payload, mime types). Different protocols get different 'channels' (analyzers). Each channel has specific permeability rules. This is stateful, content-aware filtering.",
        "rubric": [
            "Identifies cell membrane or selective permeability",
            "Applies to firewall inspection (content-aware filtering)",
            "Explains protocol-specific analysis (different channels for different protocols)",
            "Provides concrete mechanism (DPI, protocol analyzers)",
            "Addresses performance (inspection overhead) or evasion techniques",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_025_endocytosis_batch_ingestion",
        "bio_domain": "Cell Biology",
        "cs_domain": "Databases",
        "problem": "Your database ingests millions of small records per second from IoT sensors. Inserting one record at a time is slow (each insert is a transaction). Batching helps, but how do you decide batch size and timing?",
        "hint": "Cells import large molecules by wrapping them in membrane vesicles (endocytosis).",
        "gold_answer": "Apply endocytosis (bulk import via vesicles). Instead of importing molecules one by one, cells form vesicles that engulf many molecules at once. For databases: batch records into 'vesicles' (in-memory buffers). Trigger batch insert when buffer is full OR timeout expires (time-based + size-based trigger). This amortizes transaction overhead. Vesicle size = batch size tradeoff (latency vs throughput).",
        "rubric": [
            "Identifies endocytosis or bulk transport",
            "Applies to batch ingestion (buffer + flush strategy)",
            "Explains trigger conditions (size threshold, time threshold)",
            "Provides concrete mechanism (in-memory buffer, batch insert)",
            "Addresses tradeoffs (batch size vs latency)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_026_apoptosis_graceful_degradation",
        "bio_domain": "Cell Biology",
        "cs_domain": "Distributed Systems",
        "problem": "Your service has a memory leak. If you let it run, it will crash ungracefully, taking down dependent services. If you restart it preemptively, you lose in-flight work. How do you handle failing services gracefully?",
        "hint": "Cells self-destruct via programmed cell death (apoptosis) to prevent harm to surrounding tissue.",
        "gold_answer": "Apply apoptosis (programmed cell death). Monitor service health (memory usage, error rate). When thresholds are crossed, initiate graceful shutdown: (1) stop accepting new requests, (2) finish in-flight requests, (3) drain queues, (4) signal dependent services, (5) self-terminate. This prevents cascading failures. Orchestrator restarts the service cleanly. This is 'kill switch' or 'graceful degradation.'",
        "rubric": [
            "Identifies apoptosis or programmed cell death",
            "Applies to graceful shutdown (self-termination before catastrophic failure)",
            "Explains shutdown sequence (drain, signal, terminate)",
            "Provides health monitoring trigger (thresholds)",
            "Addresses dependent services (notification, fallback)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_027_synaptic_plasticity_online_learning",
        "bio_domain": "Neuroscience",
        "cs_domain": "Machine Learning",
        "problem": "Your recommendation model is trained offline on historical data. But user preferences drift over time. Retraining from scratch daily is expensive. How do you adapt the model continuously as new data arrives?",
        "hint": "Synapses strengthen or weaken based on activity (Hebbian learning: 'neurons that fire together, wire together').",
        "gold_answer": "Apply synaptic plasticity from neuroscience. Instead of retraining, use online learning with weight updates on each new sample (stochastic gradient descent). Strengthen connections (weights) for patterns that occur frequently (positive feedback). Decay unused connections (weight decay, regularization). This is continual learning. Modern approach: replay buffers, elastic weight consolidation to prevent catastrophic forgetting.",
        "rubric": [
            "Identifies synaptic plasticity or Hebbian learning",
            "Applies to online learning (incremental weight updates)",
            "Explains activity-dependent strengthening (frequent patterns reinforced)",
            "Addresses catastrophic forgetting (weight decay, replay buffers)",
            "Provides concrete mechanism (SGD, learning rate, regularization)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_028_attention_salience",
        "bio_domain": "Neuroscience",
        "cs_domain": "Information Retrieval",
        "problem": "Your search engine returns 10,000 results for a query. Users only look at the first 10. You need to rank results by relevance, but computing relevance for all 10k is expensive. How do you prioritize which results to score carefully?",
        "hint": "The brain filters sensory input—only salient stimuli reach conscious awareness.",
        "gold_answer": "Apply selective attention from neuroscience. Use a two-stage process: (1) Fast, cheap filter identifies salient candidates (e.g., keyword match, BM25), (2) Expensive, accurate ranker (neural model) scores only top candidates. This is cascade ranking. Attention is the cheap filter that selects what to process deeply. Inspiration: neural attention mechanisms in transformers.",
        "rubric": [
            "Identifies selective attention or salience filtering",
            "Applies to multi-stage ranking (cheap filter + expensive ranker)",
            "Explains cascade or funnel architecture",
            "Provides concrete mechanism (two-stage retrieval)",
            "Addresses tradeoffs (recall vs precision, speed vs accuracy)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_029_neural_pruning_compression",
        "bio_domain": "Neuroscience",
        "cs_domain": "Machine Learning",
        "problem": "Your trained neural network has 100M parameters. You want to deploy to mobile devices (limited memory). Simply reducing model size hurts accuracy. How do you shrink the model while preserving performance?",
        "hint": "During development, brains prune unused synapses (synaptic pruning).",
        "gold_answer": "Apply synaptic pruning from neuroscience. After training, identify low-importance weights (small magnitude, low gradient). Remove (zero out) these weights. Fine-tune remaining weights. This is magnitude-based pruning or lottery ticket hypothesis. Can remove 90%+ of weights with minimal accuracy loss. Pruned model is sparse, requires sparse matrix support for speedup.",
        "rubric": [
            "Identifies synaptic pruning or neural pruning",
            "Applies to model compression (remove unimportant weights)",
            "Explains importance criteria (magnitude, gradient, etc.)",
            "Provides pruning + fine-tuning workflow",
            "Addresses sparsity representation (sparse matrices, efficiency gains)",
        ],
        "max_score": 5,
    },

    {
        "id": "bio_030_neurogenesis_dynamic_capacity",
        "bio_domain": "Neuroscience",
        "cs_domain": "Distributed Systems",
        "problem": "Your service has variable load (10x traffic spikes during events). Provisioning for peak is wasteful (idle resources). Scaling takes minutes (too slow for spikes). How do you handle sudden load changes?",
        "hint": "Adult brains can generate new neurons (neurogenesis) when needed for learning.",
        "gold_answer": "Apply neurogenesis (dynamic capacity growth). Use autoscaling with predictive scaling: (1) Monitor leading indicators (social media, calendar events), (2) Pre-scale before load arrives, (3) For unpredicted spikes, use burst capacity (overprovisioning buffer). After spike, scale down (graceful neuron death). This is elastic compute with proactive + reactive scaling. Inspired by brain's ability to grow capacity on demand.",
        "rubric": [
            "Identifies neurogenesis or dynamic capacity",
            "Applies to autoscaling (grow/shrink resources on demand)",
            "Explains predictive + reactive scaling",
            "Provides concrete triggers (metrics, leading indicators)",
            "Addresses scale-down (resource reclamation, cost optimization)",
        ],
        "max_score": 5,
    },
]


def load() -> list[Question]:
    """Load BioDomain problems as Question objects."""
    questions: list[Question] = []

    for p in PROBLEMS:
        # Combine problem + hint into question text
        question_text = f"""{p['problem']}

Hint: {p['hint']}"""

        q: Question = {
            'id': p['id'],
            'question': question_text,
            'choices': [],  # Open-ended, no choices
            'answer_idx': -1,  # Not applicable for open-ended
            'answer': p['gold_answer'],  # For reference, not shown to model
            'subject': f"{p['bio_domain']} → {p['cs_domain']}",
            'extra': {
                'bio_domain': p['bio_domain'],
                'cs_domain': p['cs_domain'],
                'rubric': p['rubric'],
                'max_score': p['max_score'],
                'gold_answer': p['gold_answer'],
            }
        }
        questions.append(q)

    return questions


register(
    name='biodomain',
    load_fn=load,
    num_questions=len(PROBLEMS),
    format_fn=format_open_ended_prompt,
    description='Biology → CS cross-domain synthesis (open-ended, LLM-as-judge grading)'
)
