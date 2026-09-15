# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cross-Domain Synthesis Benchmark (XDomain-50)

Tests ability to transfer concepts from one domain to solve problems in another.
Each problem requires identifying an analogous technique from a distant domain.

Designed to test the Agent Storming hypothesis: heterogeneous panels with diverse
domain expertise should outperform single models or homogeneous panels.
"""

from __future__ import annotations
import random
from typing import TypedDict

from ..types import Question
from . import register


def format_open_ended_prompt(q: Question) -> str:
    """Format prompt for open-ended questions (no multiple choice).

    Used for XDomain benchmarks which are graded by LLM-as-judge on the full
    synthesis text, not extracted answer letter.
    """
    body = q.question.strip()
    return f"""Question ({q.subject}):
{body}

Provide a detailed answer with technical depth. Think step-by-step if helpful.
Keep your response ≤500 words."""


class XDomainProblem(TypedDict):
    """Raw problem before conversion to Question."""
    id: str
    domain_a: str  # Problem domain
    domain_b: str  # Solution domain (hint)
    problem: str
    hint: str
    gold_answer: str
    rubric: list[str]  # Evaluation criteria (each worth 1 point)
    max_score: int


# 50 cross-domain synthesis problems
PROBLEMS: list[XDomainProblem] = [
    # Compiler ↔ Database
    {
        "id": "xd001_db_compiler_cse",
        "domain_a": "Database Systems",
        "domain_b": "Compiler Optimization",
        "problem": "You're building a query optimizer. Many queries contain nested subqueries that return the same result when executed multiple times within a single query plan. How can you optimize this without changing query semantics?",
        "hint": "Consider how compilers optimize repeated computations.",
        "gold_answer": "Apply Common Subexpression Elimination (CSE) from compiler optimization. Identify identical subqueries (same normalized SQL), execute once, cache the result in a hash table keyed by the normalized form, and reuse for subsequent references. Must handle cache invalidation if underlying tables change mid-query.",
        "rubric": [
            "Identifies CSE or memoization from compiler/PL domain",
            "Correctly applies to subquery execution (caching results)",
            "Mentions normalization/canonicalization for matching",
            "Addresses cache invalidation or correctness constraints",
            "Provides concrete data structure (hash table, memo table)",
        ],
        "max_score": 5,
    },

    # ML ↔ OS
    {
        "id": "xd002_ml_os_paging",
        "domain_a": "Machine Learning",
        "domain_b": "Operating Systems",
        "problem": "Transformer models with self-attention struggle with long sequences because the attention matrix grows quadratically (O(n²)) and exhausts GPU memory. You want to support 10x longer sequences without buying more GPUs. How?",
        "hint": "Consider how operating systems handle memory larger than physical RAM.",
        "gold_answer": "Apply virtual memory / paging concepts from OS. Partition the attention matrix into blocks (pages), keep only active blocks in GPU memory (working set), and page inactive blocks to CPU RAM or disk. This is the core idea behind vLLM's PagedAttention. Requires efficient block-level attention computation and prefetching strategy.",
        "rubric": [
            "Identifies paging or virtual memory from OS domain",
            "Correctly applies to attention matrix (block/page partitioning)",
            "Explains benefit (fit larger sequences by using CPU/disk)",
            "Mentions working set or page replacement strategy",
            "Addresses performance tradeoffs (paging overhead vs memory savings)",
        ],
        "max_score": 5,
    },

    # Networking ↔ Database
    {
        "id": "xd003_db_network_congestion",
        "domain_a": "Database Systems",
        "domain_b": "Networking",
        "problem": "A distributed database has many clients issuing queries simultaneously. When load spikes, the system thrashes: queries slow down exponentially, throughput collapses, and latency explodes. How do you prevent this death spiral?",
        "hint": "Think about how the internet handles network congestion.",
        "gold_answer": "Apply TCP congestion control algorithms (e.g., AIMD - Additive Increase Multiplicative Decrease). Implement admission control: each client starts with a low query rate, increases additively when queries succeed quickly, and decreases multiplicatively on timeouts or queue saturation. This prevents overload while maximizing throughput.",
        "rubric": [
            "Identifies TCP congestion control or similar feedback mechanism",
            "Applies rate limiting or admission control to query load",
            "Explains AIMD or similar adaptive algorithm",
            "Addresses thrashing prevention (backing off under load)",
            "Mentions fairness or per-client state",
        ],
        "max_score": 5,
    },

    # Crypto ↔ Database
    {
        "id": "xd004_db_crypto_bloom",
        "domain_a": "Database Systems",
        "domain_b": "Cryptography & Data Structures",
        "problem": "You have a distributed cache with 1000 nodes. A client wants to check if key K exists anywhere in the cluster without broadcasting to all nodes (too slow). You can precompute metadata. How do you quickly narrow down which nodes might have K?",
        "hint": "Consider probabilistic data structures used in cryptography and networking.",
        "gold_answer": "Use Bloom filters. Each node maintains a Bloom filter of its keys and shares it with a coordinator. To check if K exists, query all Bloom filters (small, fast). Filters with negatives are ruled out; filters with positives are candidates (may have false positives). Only query candidate nodes. Tradeoff: space (filter size) vs false positive rate.",
        "rubric": [
            "Identifies Bloom filter or similar probabilistic structure",
            "Correctly applies to distributed cache membership test",
            "Explains false positives but no false negatives property",
            "Describes precomputation and query phases",
            "Mentions space/accuracy tradeoff",
        ],
        "max_score": 5,
    },

    # Game Theory ↔ Networking
    {
        "id": "xd005_net_game_routing",
        "domain_a": "Networking",
        "domain_b": "Game Theory",
        "problem": "In a peer-to-peer network, each node can route packets for others, but routing costs bandwidth. Selfish nodes might refuse to route (free-rider problem). How do you incentivize cooperation without a central authority?",
        "hint": "Consider game-theoretic mechanisms for cooperation in repeated interactions.",
        "gold_answer": "Apply Tit-for-Tat or reputation-based mechanisms from iterated prisoner's dilemma. Each node tracks others' cooperation (how often they routed packets). Nodes preferentially route for cooperators and drop packets from free-riders. In equilibrium, cooperation is the Nash strategy. Alternatively, use a token/credit system where routing earns tokens spent when your packets need routing.",
        "rubric": [
            "Identifies game theory concept (Tit-for-Tat, reputation, iterated PD)",
            "Applies to routing incentive problem",
            "Explains how cooperation becomes equilibrium",
            "Addresses Sybil attacks or collusion (or acknowledges limitation)",
            "Provides concrete mechanism (reputation scores, tokens, etc.)",
        ],
        "max_score": 5,
    },

    # Biology ↔ Algorithms
    {
        "id": "xd006_algo_bio_genetic",
        "domain_a": "Algorithm Design",
        "domain_b": "Biology (Evolution)",
        "problem": "You need to optimize a complex objective function with 1000 parameters, multiple local optima, and no gradient information (black-box optimization). Standard hill-climbing gets stuck. How do you escape local optima and find a good solution?",
        "hint": "Think about how nature solves complex optimization problems.",
        "gold_answer": "Apply Genetic Algorithms from evolutionary biology. Maintain a population of candidate solutions (parameter vectors). Each generation: (1) select top performers (fitness), (2) crossover (recombine parameters from pairs), (3) mutate (random changes), (4) replace population. Crossover explores combinations; mutation prevents stagnation. Population diversity helps escape local optima.",
        "rubric": [
            "Identifies genetic algorithm or evolutionary strategy",
            "Explains population, fitness, selection",
            "Describes crossover and mutation operators",
            "Explains how diversity helps escape local optima",
            "Mentions tradeoffs (slow convergence, hyperparameter tuning)",
        ],
        "max_score": 5,
    },

    # Physics ↔ ML
    {
        "id": "xd007_ml_physics_momentum",
        "domain_a": "Machine Learning",
        "domain_b": "Physics (Mechanics)",
        "problem": "Gradient descent on neural networks often oscillates in narrow valleys (high curvature) and converges slowly on flat surfaces (low curvature). How can you accelerate convergence by smoothing these dynamics?",
        "hint": "Consider how physical objects move through space under forces.",
        "gold_answer": "Apply momentum from Newtonian mechanics. Maintain a velocity vector (exponential moving average of past gradients). Update: v = β*v - lr*grad; θ += v. Momentum smooths oscillations (opposing forces cancel) and accelerates through flat regions (velocity accumulates). This is SGD with momentum or Adam's moving average.",
        "rubric": [
            "Identifies momentum or inertia from physics",
            "Correctly applies to gradient descent (velocity = moving avg of gradients)",
            "Explains benefit (smoothing oscillations, accelerating convergence)",
            "Provides update rule or concrete algorithm",
            "Mentions hyperparameter β (momentum coefficient)",
        ],
        "max_score": 5,
    },

    # Economics ↔ Database
    {
        "id": "xd008_db_econ_auction",
        "domain_a": "Database Systems (Cloud)",
        "domain_b": "Economics (Auctions)",
        "problem": "A cloud database has spare capacity (CPU, memory) that varies by minute. You want to sell this spare capacity to batch jobs without disrupting latency-sensitive transactions. How do you price it dynamically to maximize revenue while ensuring batch jobs don't starve transactions?",
        "hint": "Think about mechanisms for allocating scarce resources when buyers have different values.",
        "gold_answer": "Apply auction theory, specifically second-price auctions (Vickrey). Batch jobs bid their value per unit capacity. System runs an auction each minute: allocates spare capacity to highest bidders, charges the second-highest bid price. Truthful bidding is incentive-compatible. Transactions have implicit infinite bid (always win). Revenue-maximizes while ensuring efficiency.",
        "rubric": [
            "Identifies auction mechanism from economics",
            "Applies to dynamic resource allocation",
            "Explains bidding process (batch jobs bid, transactions prioritized)",
            "Mentions incentive compatibility or truthfulness (second-price)",
            "Addresses revenue maximization and efficiency",
        ],
        "max_score": 5,
    },

    # Compiler ↔ Security
    {
        "id": "xd009_sec_compiler_taint",
        "domain_a": "Security (Web Applications)",
        "domain_b": "Compiler Analysis",
        "problem": "A web app takes user input and uses it in SQL queries and HTML output. You want to automatically detect if unsanitized user input reaches a dangerous sink (SQL execution, HTML rendering) without manually auditing every code path. How?",
        "hint": "Consider how compilers track data flow through programs.",
        "gold_answer": "Apply taint analysis from compiler security analysis. Mark user input as 'tainted'. Propagate taint through data flow (assignments, string concatenation). Flag an error if tainted data reaches a dangerous sink without sanitization. This is static or dynamic taint tracking. Tools like FlowDroid use this for Android security.",
        "rubric": [
            "Identifies taint analysis or dataflow analysis",
            "Correctly applies to tracking user input",
            "Explains taint sources (input) and sinks (SQL, HTML)",
            "Describes propagation through operations",
            "Mentions sanitization as taint removal",
        ],
        "max_score": 5,
    },

    # Networking ↔ OS
    {
        "id": "xd010_os_net_packet_process",
        "domain_a": "Operating Systems",
        "domain_b": "Networking",
        "problem": "A multi-threaded application has 1000 threads frequently acquiring and releasing a single shared lock. Lock contention is causing terrible performance. Threads mostly read shared data, rarely write. How can you reduce contention?",
        "hint": "Think about how routers handle broadcast messages vs unicast.",
        "gold_answer": "Apply multicast/broadcast concepts by using Read-Write Locks (or RCU - Read-Copy-Update). Multiple readers can hold the lock concurrently (like multicast to many receivers), but writers get exclusive access (unicast to one). For RCU: readers access data without locks; writers create a new version, atomically swap pointer, defer freeing old version until no readers remain.",
        "rubric": [
            "Identifies read-write lock or RCU",
            "Connects to networking concept (multicast, broadcast, many-to-one)",
            "Explains multiple readers, exclusive writer",
            "Addresses read-heavy workload optimization",
            "Mentions tradeoffs (write overhead, stale reads in RCU)",
        ],
        "max_score": 5,
    },

    # ML ↔ Cryptography
    {
        "id": "xd011_ml_crypto_differential",
        "domain_a": "Machine Learning (Privacy)",
        "domain_b": "Cryptography",
        "problem": "You train a model on sensitive medical records and publish it. An attacker can query your model and infer if a specific patient's record was in the training set (membership inference attack). How do you prevent this while still publishing the model?",
        "hint": "Consider cryptographic techniques for privacy-preserving data release.",
        "gold_answer": "Apply Differential Privacy from cryptography. Add calibrated noise to training (e.g., DP-SGD adds noise to gradients) such that the model's output distribution is nearly identical whether any single patient is included or excluded. Privacy guarantee: ε-DP ensures bounded information leakage. Tradeoff: noise reduces model accuracy.",
        "rubric": [
            "Identifies differential privacy or similar privacy mechanism",
            "Correctly applies to training (DP-SGD, noisy gradients)",
            "Explains privacy guarantee (indistinguishability, bounded leakage)",
            "Mentions noise addition mechanism",
            "Addresses accuracy/privacy tradeoff",
        ],
        "max_score": 5,
    },

    # Database ↔ Algorithms
    {
        "id": "xd012_db_algo_lsm",
        "domain_a": "Database Systems",
        "domain_b": "Algorithm Design (Sorting)",
        "problem": "A key-value store handles a write-heavy workload. Random writes to a B-tree are slow (each write = disk seek). Reads are less frequent. You want to optimize write throughput by orders of magnitude. How?",
        "hint": "Think about efficient strategies for merging sorted sequences.",
        "gold_answer": "Apply merge-sort concepts via Log-Structured Merge Trees (LSM trees). Buffer writes in memory (memtable), flush to immutable sorted files (SSTables) on disk. Reads check memtable then SSTables (may need multiple seeks). Periodically merge SSTables (k-way merge) to compact. Writes are fast (sequential, batched); reads slower but acceptable for write-heavy workloads.",
        "rubric": [
            "Identifies merge-sort or LSM tree structure",
            "Explains buffering writes in memory, flushing to sorted files",
            "Describes read process (check multiple levels)",
            "Explains compaction via merging sorted files",
            "Addresses write-heavy optimization tradeoff",
        ],
        "max_score": 5,
    },

    # Game Theory ↔ ML
    {
        "id": "xd013_ml_game_adversarial",
        "domain_a": "Machine Learning (Generative Models)",
        "domain_b": "Game Theory",
        "problem": "You want to train a generator network to produce realistic images, but you don't have an explicit objective function for 'realistic'. How can you train the generator without hand-crafted loss functions?",
        "hint": "Consider scenarios where two players compete against each other.",
        "gold_answer": "Apply zero-sum game theory via Generative Adversarial Networks (GANs). Train two networks: generator (creates images) and discriminator (classifies real vs fake). They play a minimax game: generator tries to fool discriminator, discriminator tries to detect fakes. At Nash equilibrium, generator produces realistic images. Loss is adversarial, not hand-crafted.",
        "rubric": [
            "Identifies game theory (zero-sum, minimax, adversarial)",
            "Correctly describes GAN architecture (generator vs discriminator)",
            "Explains adversarial training (generator fools discriminator)",
            "Mentions Nash equilibrium or convergence",
            "Addresses benefit (no explicit realism metric needed)",
        ],
        "max_score": 5,
    },

    # Networking ↔ Algorithms
    {
        "id": "xd014_algo_net_sliding_window",
        "domain_a": "Algorithm Design",
        "domain_b": "Networking (TCP)",
        "problem": "You're processing a stream of sensor readings and need to compute the maximum value in the last 1000 readings, updated for every new reading. A naive approach recomputes max(last 1000) each time, which is slow. How do you maintain this efficiently?",
        "hint": "Think about how reliable network protocols manage outstanding packets.",
        "gold_answer": "Apply TCP's sliding window concept via a monotonic deque. Maintain a deque of (value, timestamp) pairs in decreasing order. On new reading: (1) remove expired entries (older than 1000 ago) from front, (2) remove smaller values from back (they'll never be max), (3) append new reading. Front of deque is always the max. O(1) amortized per reading.",
        "rubric": [
            "Identifies sliding window from networking",
            "Correctly applies to streaming max problem",
            "Describes deque or similar data structure",
            "Explains maintenance algorithm (expire old, remove dominated)",
            "Mentions O(1) or O(n) amortized complexity",
        ],
        "max_score": 5,
    },

    # Biology ↔ Networking
    {
        "id": "xd015_net_bio_epidemic",
        "domain_a": "Networking (Content Distribution)",
        "domain_b": "Biology (Epidemiology)",
        "problem": "A video streaming service wants to distribute a new movie to millions of users. Serving from a central server is too slow and expensive. How can you distribute the content efficiently by leveraging users themselves?",
        "hint": "Consider how diseases spread through a population.",
        "gold_answer": "Apply epidemic/gossip protocols from epidemiology. Seed the movie to a few initial peers. Each peer that receives the movie randomly shares it with others (BitTorrent's tit-for-tat, gossip propagation). Content spreads exponentially like an infection. Load distributes across peers; bandwidth scales with user count. Requires incentive mechanisms to ensure sharing.",
        "rubric": [
            "Identifies epidemic/gossip/viral spreading model",
            "Correctly applies to peer-to-peer distribution (BitTorrent, CDN)",
            "Explains exponential spread (each peer infects others)",
            "Mentions bandwidth scaling benefit",
            "Addresses incentive problem (leeching, free-riding)",
        ],
        "max_score": 5,
    },

    # Physics ↔ Algorithms
    {
        "id": "xd016_algo_physics_simulated_annealing",
        "domain_a": "Algorithm Design (Optimization)",
        "domain_b": "Physics (Thermodynamics)",
        "problem": "You're solving the traveling salesman problem (TSP). Hill climbing finds local optima quickly but gets stuck. You need an algorithm that can escape local optima and find a near-optimal solution for large instances. How?",
        "hint": "Think about how metals reach low-energy crystalline structures when cooled.",
        "gold_answer": "Apply simulated annealing from thermodynamics. Start with high 'temperature' T, allowing uphill moves (worse solutions) with probability exp(-ΔE/T). Gradually cool T. High T explores broadly (escapes local optima); low T converges to optimum. Mimics metal annealing: atoms settle into low-energy states.",
        "rubric": [
            "Identifies simulated annealing from physics",
            "Explains temperature parameter and cooling schedule",
            "Describes acceptance probability for uphill moves (Boltzmann distribution)",
            "Connects to metal annealing process (atoms, energy states)",
            "Explains how cooling balances exploration vs exploitation",
        ],
        "max_score": 5,
    },

    # Compiler ↔ ML
    {
        "id": "xd017_ml_compiler_fusion",
        "domain_a": "Machine Learning (Training)",
        "domain_b": "Compiler Optimization",
        "problem": "Training a neural network involves many small tensor operations (matrix multiplies, activations, normalizations). Each operation launches a separate GPU kernel, causing overhead from memory reads/writes. How do you reduce this overhead?",
        "hint": "Consider how compilers optimize sequences of operations.",
        "gold_answer": "Apply loop fusion / operator fusion from compiler optimization. Detect chains of operations (e.g., matmul → bias → relu) and fuse into a single GPU kernel. Intermediate results stay in registers/cache, avoiding slow global memory traffic. This is used in XLA, TensorRT, and PyTorch's JIT. Tradeoff: kernel complexity vs memory bandwidth.",
        "rubric": [
            "Identifies loop fusion or operator fusion from compilers",
            "Correctly applies to deep learning (fusing ops into single kernel)",
            "Explains memory bandwidth benefit (avoid global memory roundtrips)",
            "Mentions tensor compilers (XLA, TensorRT, TVM, etc.)",
            "Addresses tradeoff (kernel complexity, register pressure)",
        ],
        "max_score": 5,
    },

    # Cryptography ↔ Database
    {
        "id": "xd018_db_crypto_merkle",
        "domain_a": "Database Systems (Replication)",
        "domain_b": "Cryptography",
        "problem": "Two replicas of a database have diverged due to network partitions. You want to efficiently find which records differ without transferring the entire dataset (terabytes). How do you quickly identify the deltas?",
        "hint": "Consider cryptographic structures for verifying data integrity.",
        "gold_answer": "Apply Merkle trees from cryptography. Build a hash tree over sorted keys: leaves = hash(record), internal nodes = hash(children). Compare root hashes; if differ, recursively compare subtrees. Only differing subtrees are transferred. Used in Cassandra, Dynamo, Bitcoin. O(log n) comparison depth vs O(n) naive scan.",
        "rubric": [
            "Identifies Merkle tree or hash tree",
            "Correctly applies to replica sync (anti-entropy)",
            "Explains tree structure (leaf = record hash, internal = combined hash)",
            "Describes comparison algorithm (root down to differing leaves)",
            "Mentions O(log n) or logarithmic comparison benefit",
        ],
        "max_score": 5,
    },

    # Economics ↔ Networking
    {
        "id": "xd019_net_econ_pricing",
        "domain_a": "Networking (ISP)",
        "domain_b": "Economics (Pricing)",
        "problem": "An ISP's network is congested during peak hours (evening streaming) but underutilized at night. Flat-rate pricing leads to overuse at peak. How can you use pricing to shift demand and reduce congestion without losing customers?",
        "hint": "Consider economic mechanisms for managing demand for scarce goods.",
        "gold_answer": "Apply time-of-use pricing or congestion pricing from economics. Charge higher rates during peak hours, lower rates during off-peak. Elastic-demand users shift usage; inelastic users pay more. Balances load over time, increases efficiency, maintains revenue. Similar to electricity pricing or Uber surge pricing.",
        "rubric": [
            "Identifies time-of-use or congestion pricing",
            "Applies to network bandwidth allocation",
            "Explains demand shifting (elastic users move to off-peak)",
            "Mentions fairness or revenue-neutrality considerations",
            "Provides example (electricity, surge pricing, toll roads)",
        ],
        "max_score": 5,
    },

    # OS ↔ ML
    {
        "id": "xd020_ml_os_checkpointing",
        "domain_a": "Machine Learning (Training)",
        "domain_b": "Operating Systems",
        "problem": "Training a large model takes weeks. If training crashes (GPU failure, OOM, software bug), you lose all progress. How do you ensure you can resume training without starting from scratch?",
        "hint": "Think about how operating systems handle process failures.",
        "gold_answer": "Apply checkpointing from OS fault tolerance. Periodically save model weights, optimizer state, and step counter to disk (checkpoint). On crash, restore latest checkpoint and resume. Tradeoff: checkpoint frequency vs overhead. Used in all major ML frameworks (PyTorch, TensorFlow). Similar to OS process snapshots or database WAL.",
        "rubric": [
            "Identifies checkpointing or snapshotting from OS",
            "Correctly applies to ML training (save weights, optimizer state)",
            "Explains resume process (restore checkpoint, continue)",
            "Mentions checkpoint frequency tradeoff",
            "Addresses state to save (model, optimizer, RNG, step count)",
        ],
        "max_score": 5,
    },

    # Algorithms ↔ Biology
    {
        "id": "xd021_algo_bio_dynamic_programming",
        "domain_a": "Biology (Genomics)",
        "domain_b": "Algorithm Design",
        "problem": "You have two DNA sequences and need to find the best alignment (which positions match, which are insertions/deletions). Trying all alignments is exponential. How do you find the optimal alignment efficiently?",
        "hint": "Consider algorithmic techniques for problems with overlapping subproblems.",
        "gold_answer": "Apply dynamic programming. Sequence alignment is Needleman-Wunsch or Smith-Waterman algorithm. Build table DP[i][j] = best score aligning prefix of length i and j. Recurrence: match/mismatch or gap. Traceback gives alignment. O(n²) time vs O(2^n) brute force. DP is fundamental to bioinformatics.",
        "rubric": [
            "Identifies dynamic programming from algorithms",
            "Correctly applies to sequence alignment",
            "Explains DP table and recurrence relation",
            "Mentions Needleman-Wunsch or Smith-Waterman",
            "Explains complexity (O(n²) vs exponential brute force)",
        ],
        "max_score": 5,
    },

    # Networking ↔ Database
    {
        "id": "xd022_db_net_load_balancing",
        "domain_a": "Database Systems (Sharding)",
        "domain_b": "Networking (Load Balancing)",
        "problem": "A database is sharded across 100 servers by hashing user_id. Adding or removing servers causes most data to rehash to different servers (massive data migration). How can you minimize reshuffling when the cluster size changes?",
        "hint": "Consider how load balancers distribute traffic when backend servers change.",
        "gold_answer": "Apply consistent hashing from networking. Hash servers and keys onto a ring [0, 2^32). Key K goes to first server clockwise from hash(K). Adding/removing server S only affects keys in the arc between S and its predecessor (~1/N keys). Much better than modulo hashing (affects all keys). Used in Dynamo, Cassandra, Memcached.",
        "rubric": [
            "Identifies consistent hashing from networking/distributed systems",
            "Correctly applies to database sharding",
            "Explains hash ring concept (servers and keys on circle)",
            "Describes minimal reshuffling property (O(1/N) vs O(1))",
            "Mentions virtual nodes or replication strategy",
        ],
        "max_score": 5,
    },

    # Compiler ↔ Database
    {
        "id": "xd023_db_compiler_query_optimization",
        "domain_a": "Database Systems",
        "domain_b": "Compiler Optimization",
        "problem": "A SQL query can be executed in many ways (join orders, index usage). Trying all plans is exponential. How do you quickly find a good execution plan without exhaustive search?",
        "hint": "Think about how compilers choose instruction sequences efficiently.",
        "gold_answer": "Apply dynamic programming (like compilers' instruction selection). Break query into subproblems (join subtrees). For each subset of tables, find optimal join tree using DP. Recurrence: optimal(S) = min over splits {cost(S1 join S2) + optimal(S1) + optimal(S2)}. This is the System R / Selinger optimizer. O(n²^n) vs O(n!) exhaustive.",
        "rubric": [
            "Identifies DP or cost-based optimization from compilers",
            "Correctly applies to query optimization (join ordering)",
            "Explains DP over table subsets",
            "Mentions cost model (I/O, selectivity, cardinality)",
            "References System R or Volcano/Cascades framework",
        ],
        "max_score": 5,
    },

    # Game Theory ↔ OS
    {
        "id": "xd024_os_game_scheduling",
        "domain_a": "Operating Systems (Scheduling)",
        "domain_b": "Game Theory",
        "problem": "A cloud OS schedules jobs from multiple tenants on shared CPUs. Tenants can lie about their job's runtime to get priority. How do you design a scheduler that incentivizes truthful reporting?",
        "hint": "Consider auction mechanisms where truth-telling is optimal.",
        "gold_answer": "Apply mechanism design from game theory. Use strategyproof scheduling: charge each job based on the opportunity cost it imposes on others (Vickrey-Clarke-Groves mechanism). Truthfully reporting runtime is a dominant strategy. Alternatively, use shortest-job-first with penalties for underestimation. Makes lying unprofitable.",
        "rubric": [
            "Identifies mechanism design or incentive compatibility from game theory",
            "Applies to OS scheduling (truthful runtime reporting)",
            "Explains VCG or strategyproof mechanism",
            "Describes incentive structure (lying hurts the liar)",
            "Mentions dominant strategy or Nash equilibrium",
        ],
        "max_score": 5,
    },

    # Physics ↔ Networking
    {
        "id": "xd025_net_physics_wave",
        "domain_a": "Networking (Wireless)",
        "domain_b": "Physics (Wave Mechanics)",
        "problem": "Multiple WiFi devices transmit simultaneously on the same frequency. Their signals interfere destructively, causing garbled reception. How can you enable multiple simultaneous transmissions without interference?",
        "hint": "Think about how waves can be separated despite overlapping in space.",
        "gold_answer": "Apply wave superposition and orthogonality from physics. Use Orthogonal Frequency Division Multiplexing (OFDM): split channel into many narrow subcarriers with orthogonal frequencies. Multiple devices transmit on different subcarriers simultaneously. Orthogonality ensures no interference (inner product = 0). Used in WiFi, LTE, 5G.",
        "rubric": [
            "Identifies wave orthogonality or superposition from physics",
            "Correctly applies to wireless multiplexing (OFDM, FDMA)",
            "Explains orthogonal subcarriers or frequency division",
            "Describes interference cancellation via orthogonality",
            "Mentions OFDM or similar technique (CDMA, MIMO)",
        ],
        "max_score": 5,
    },

    # ML ↔ Economics
    {
        "id": "xd026_ml_econ_bandit",
        "domain_a": "Machine Learning (Online Learning)",
        "domain_b": "Economics (Decision Theory)",
        "problem": "A recommendation system must choose which ad to show a user. It doesn't know which ad performs best. Showing the same ad repeatedly gathers data but loses revenue if it's not the best. How do you balance learning and earning?",
        "hint": "Consider economic frameworks for exploration vs exploitation tradeoffs.",
        "gold_answer": "Apply multi-armed bandit theory from economics/decision theory. Model as bandit: each ad is an arm with unknown reward distribution. Use algorithms like UCB (Upper Confidence Bound) or Thompson Sampling that balance exploration (try uncertain arms) and exploitation (choose known good arms). Regret bound quantifies learning cost.",
        "rubric": [
            "Identifies multi-armed bandit or exploration-exploitation tradeoff",
            "Correctly applies to ad selection or recommendation",
            "Explains exploration (try uncertain options) vs exploitation (pick best known)",
            "Mentions specific algorithm (UCB, Thompson Sampling, epsilon-greedy)",
            "Discusses regret or convergence to optimal arm",
        ],
        "max_score": 5,
    },

    # Cryptography ↔ Networking
    {
        "id": "xd027_net_crypto_onion",
        "domain_a": "Networking (Privacy)",
        "domain_b": "Cryptography",
        "problem": "A user wants to browse the web anonymously. The ISP can see which websites they visit. Existing VPNs just shift trust to the VPN provider. How can you hide browsing activity from any single observer?",
        "hint": "Think about cryptographic techniques for layered encryption.",
        "gold_answer": "Apply onion routing from cryptography (Tor). User encrypts request in layers: E_A(E_B(E_C(request))). Each relay decrypts one layer, sees only previous/next hop, not origin or destination. No single relay knows both. Requires multiple hops. Tradeoff: latency vs anonymity.",
        "rubric": [
            "Identifies onion routing or Tor",
            "Explains layered encryption (each relay decrypts one layer)",
            "Describes unlinkability (no single observer sees full path)",
            "Mentions multiple hops and tradeoffs (latency, bandwidth)",
            "Addresses trust distribution (no single trusted party)",
        ],
        "max_score": 5,
    },

    # Algorithms ↔ Game Theory
    {
        "id": "xd028_algo_game_minimax",
        "domain_a": "Algorithm Design (Game Playing)",
        "domain_b": "Game Theory",
        "problem": "You're building a chess AI. The game tree is enormous (10^120 positions). You need to choose the best move given limited computation. How do you search the tree efficiently without examining every position?",
        "hint": "Consider zero-sum game strategies for adversarial opponents.",
        "gold_answer": "Apply minimax algorithm from game theory with alpha-beta pruning. Recursively evaluate: your turn maximizes score, opponent's turn minimizes. Prune branches where score bounds prove the branch can't affect the root decision. Combined with depth limit and evaluation function. Core of traditional chess engines before deep learning.",
        "rubric": [
            "Identifies minimax from game theory",
            "Correctly applies to game tree search",
            "Explains alternating max/min layers (player vs opponent)",
            "Describes alpha-beta pruning (branch elimination)",
            "Mentions depth limit or evaluation function for practical use",
        ],
        "max_score": 5,
    },

    # Database ↔ OS
    {
        "id": "xd029_db_os_wal",
        "domain_a": "Database Systems (Crash Recovery)",
        "domain_b": "Operating Systems (File Systems)",
        "problem": "A database writes data to disk. A crash during a write can leave data corrupted (partial writes, torn pages). After crash, you need to restore a consistent state. How do you ensure atomicity and durability?",
        "hint": "Consider how file systems ensure consistency after crashes.",
        "gold_answer": "Apply write-ahead logging (WAL) from OS journaling file systems. Before modifying data, append change to a sequential log on disk. Flush log, then apply change to data pages. On crash, replay log to restore committed transactions. Redo log for committed work, undo log for uncommitted. Used in PostgreSQL, MySQL, ext4, NTFS.",
        "rubric": [
            "Identifies write-ahead logging or journaling",
            "Correctly applies to database recovery (redo/undo logs)",
            "Explains log-first ordering (log flushed before data page)",
            "Describes recovery process (replay log after crash)",
            "Connects to file system journaling (ext3/4, NTFS)",
        ],
        "max_score": 5,
    },

    # ML ↔ Compiler
    {
        "id": "xd030_ml_compiler_autograd",
        "domain_a": "Machine Learning (Training)",
        "domain_b": "Compiler Design",
        "problem": "Computing gradients for backpropagation by hand is error-prone. For complex models with hundreds of layers, it's infeasible. How can you automatically compute exact gradients for arbitrary neural network architectures?",
        "hint": "Think about compiler techniques for automatic program transformation.",
        "gold_answer": "Apply automatic differentiation (autodiff) from compiler techniques. Build a computation graph (IR). For forward pass f(x), generate backward pass via the chain rule: df/dx = (df/dy) * (dy/dx) for each operation. Reverse-mode AD (backprop) accumulates gradients backward. Forward-mode for Jacobian-vector products. Used in PyTorch, JAX, TensorFlow.",
        "rubric": [
            "Identifies automatic differentiation or autodiff",
            "Connects to compiler IR or program transformation",
            "Explains reverse-mode AD (backpropagation is a special case)",
            "Describes chain rule application to computation graph",
            "Mentions forward vs reverse mode or specific frameworks",
        ],
        "max_score": 5,
    },

    # Biology ↔ Security
    {
        "id": "xd031_sec_bio_immune",
        "domain_a": "Security (Intrusion Detection)",
        "domain_b": "Biology (Immunology)",
        "problem": "A network intrusion detection system must distinguish normal traffic from attacks. Attackers constantly evolve tactics (polymorphism, obfuscation). Signature-based detection fails against novel attacks. How can you detect unknown attacks?",
        "hint": "Consider how biological systems detect novel pathogens.",
        "gold_answer": "Apply immune system principles: anomaly detection via negative selection. Model normal behavior (self), flag significant deviations (non-self = attacks). Artificial immune systems (AIS) use detectors trained on normal data, recognizing outliers. Adaptive: detectors evolve as attack patterns change. Inspired by T-cell maturation.",
        "rubric": [
            "Identifies artificial immune system or anomaly detection",
            "Connects to biological immune system (self/non-self)",
            "Explains negative selection or outlier detection",
            "Describes adaptive or evolving detector set",
            "Addresses novel attack detection (not just known signatures)",
        ],
        "max_score": 5,
    },

    # Physics ↔ Database
    {
        "id": "xd032_db_physics_entropy",
        "domain_a": "Database Systems (Compression)",
        "domain_b": "Physics (Information Theory)",
        "problem": "A database stores billions of log entries. Storage is expensive. Logs are highly redundant (repeated patterns, common fields). How do you determine the theoretical limit of how much you can compress the logs?",
        "hint": "Consider physical limits on information content.",
        "gold_answer": "Apply Shannon entropy from information theory (physics of communication). Entropy H = -Σ p(x) log p(x) gives average bits per symbol. For logs, measure entropy of field distributions. Compression algorithms approach but can't beat entropy (lower bound). Huffman coding, arithmetic coding achieve near-entropy compression. Entropy quantifies irreducible information content.",
        "rubric": [
            "Identifies Shannon entropy or information theory",
            "Correctly applies to data compression limits",
            "Explains entropy as theoretical lower bound (bits per symbol)",
            "Mentions compression algorithms approaching entropy (Huffman, arithmetic)",
            "Connects to physics (thermodynamic entropy, information = physical)",
        ],
        "max_score": 5,
    },

    # Networking ↔ Compiler
    {
        "id": "xd033_net_compiler_packet_vectorize",
        "domain_a": "Networking (Packet Processing)",
        "domain_b": "Compiler Optimization",
        "problem": "A software router processes packets one at a time. Each packet requires parsing headers, lookup in routing table, TTL decrement, checksum update. Throughput is limited by per-packet overhead. How can you process multiple packets more efficiently?",
        "hint": "Consider how compilers optimize loops that operate on arrays.",
        "gold_answer": "Apply vectorization / SIMD from compilers. Batch multiple packets, process in parallel using SIMD instructions (e.g., AVX-512). Parse 8 packets' headers simultaneously, do 8 routing lookups in parallel, update 8 checksums in one instruction. This is batched packet processing in DPDK, VPP. Amortizes overhead, exploits data parallelism.",
        "rubric": [
            "Identifies vectorization or SIMD from compilers",
            "Correctly applies to packet processing (batching)",
            "Explains parallel processing of multiple packets",
            "Mentions SIMD instructions (SSE, AVX, NEON)",
            "Addresses throughput improvement (amortized overhead, parallelism)",
        ],
        "max_score": 5,
    },

    # Economics ↔ ML
    {
        "id": "xd034_ml_econ_shapley",
        "domain_a": "Machine Learning (Model Interpretability)",
        "domain_b": "Economics (Cooperative Game Theory)",
        "problem": "A black-box model predicts loan approval. Regulators require explaining how much each feature (income, credit score, age) contributed to a specific prediction. How do you fairly attribute the prediction to features?",
        "hint": "Consider economic methods for fairly distributing value among contributors.",
        "gold_answer": "Apply Shapley values from cooperative game theory. For each feature, compute average marginal contribution over all possible feature subsets. Shapley value = fair share of prediction. Satisfies axioms: efficiency (sums to prediction), symmetry, dummy, additivity. SHAP (SHapley Additive exPlanations) implements this for ML.",
        "rubric": [
            "Identifies Shapley values or cooperative game theory",
            "Correctly applies to feature attribution (SHAP)",
            "Explains marginal contribution averaging over subsets",
            "Mentions fairness axioms or properties",
            "Discusses computational cost (exponential, approximations)",
        ],
        "max_score": 5,
    },

    # Cryptography ↔ Algorithms
    {
        "id": "xd035_algo_crypto_commitment",
        "domain_a": "Algorithm Design (Protocol Design)",
        "domain_b": "Cryptography",
        "problem": "Two parties want to play rock-paper-scissors remotely without a trusted referee. If one reveals their choice first, the other can cheat. How can they commit to choices simultaneously without revealing them until both are committed?",
        "hint": "Consider cryptographic primitives for binding commitments.",
        "gold_answer": "Apply commitment schemes from cryptography. Each player computes commitment C = hash(choice || nonce) and sends C. After both commit, reveal choice and nonce; other verifies hash matches. Binding (can't change choice after commitment) and hiding (can't deduce choice from commitment). Used in coin-flipping protocols, smart contracts.",
        "rubric": [
            "Identifies cryptographic commitment scheme",
            "Correctly applies to simultaneous reveal protocol",
            "Explains commit phase (hash + nonce) and reveal phase",
            "Describes binding and hiding properties",
            "Mentions applications (coin flip, auctions, blockchain)",
        ],
        "max_score": 5,
    },

    # OS ↔ Networking
    {
        "id": "xd036_os_net_bufferbloat",
        "domain_a": "Operating Systems (Buffer Management)",
        "domain_b": "Networking",
        "problem": "A kernel network stack has large send/receive buffers (improves throughput). But when buffers fill during congestion, latency spikes to seconds (bufferbloat). Small interactive packets wait behind bulk data. How do you reduce latency without sacrificing throughput?",
        "hint": "Consider queue management algorithms from networking research.",
        "gold_answer": "Apply Active Queue Management (AQM) from networking, e.g., CoDel or fq_codel. Drop or mark packets before buffer is full to signal congestion early. FQ (Fair Queueing) separates flows; CoDel (Controlled Delay) drops packets when queuing delay exceeds target. Keeps buffer shallow, latency low, throughput high.",
        "rubric": [
            "Identifies AQM (CoDel, RED, fq_codel)",
            "Correctly applies to buffer management (drop before full)",
            "Explains early congestion signaling (ECN, drops)",
            "Mentions fairness (per-flow queuing) or delay target",
            "Addresses latency/throughput tradeoff",
        ],
        "max_score": 5,
    },

    # Game Theory ↔ Cryptography
    {
        "id": "xd037_crypto_game_byzantine",
        "domain_a": "Cryptography (Consensus)",
        "domain_b": "Game Theory",
        "problem": "A blockchain needs distributed consensus: nodes must agree on transaction order despite some nodes being malicious (Byzantine faults). Malicious nodes may lie, collude, or equivocate. How do you achieve agreement when up to 1/3 of nodes are adversarial?",
        "hint": "Consider game-theoretic mechanisms for cooperation under adversarial conditions.",
        "gold_answer": "Apply Byzantine fault tolerance (BFT) algorithms, which combine cryptography (signatures, quorum certificates) with game theory (honest majority assumption, incentive alignment). Protocols like PBFT, Tendermint use voting rounds where >2/3 agreement overcomes 1/3 adversaries. Economic incentives (rewards, slashing) ensure rational actors behave honestly.",
        "rubric": [
            "Identifies Byzantine fault tolerance or consensus",
            "Connects to game theory (adversarial model, incentives)",
            "Explains >2/3 honest assumption or quorum requirement",
            "Describes voting/rounds mechanism (PBFT, Tendermint, HotStuff)",
            "Mentions incentives (staking, slashing, rewards)",
        ],
        "max_score": 5,
    },

    # Algorithms ↔ Economics
    {
        "id": "xd038_algo_econ_stable_matching",
        "domain_a": "Algorithm Design (Matching)",
        "domain_b": "Economics (Market Design)",
        "problem": "A medical school assigns residents to hospitals. Each resident ranks hospitals; each hospital ranks residents. A naive assignment may result in a resident and hospital both preferring each other to their assigned match (instability). How do you compute a stable assignment where no pair wants to defect?",
        "hint": "Consider economic mechanisms for two-sided markets.",
        "gold_answer": "Apply stable matching via Gale-Shapley (deferred acceptance) algorithm from economics. Residents propose to hospitals in preference order; hospitals tentatively accept best proposals, reject others. Iterate until stable. Produces a stable matching (no blocking pairs). Used in NRMP (medical residency), school choice.",
        "rubric": [
            "Identifies stable matching or Gale-Shapley algorithm",
            "Correctly applies to two-sided matching (residents, hospitals)",
            "Explains deferred acceptance algorithm (proposals, rejections)",
            "Defines stability (no blocking pairs)",
            "Mentions real applications (NRMP, school choice, kidney exchange)",
        ],
        "max_score": 5,
    },

    # Biology ↔ Algorithms
    {
        "id": "xd039_algo_bio_phylogeny",
        "domain_a": "Algorithm Design (Tree Construction)",
        "domain_b": "Biology (Evolution)",
        "problem": "You have DNA sequences from 100 species and want to infer their evolutionary tree (phylogeny). The tree has 100! possible topologies. How do you find the most likely tree efficiently?",
        "hint": "Consider algorithmic techniques for building hierarchical structures from pairwise similarities.",
        "gold_answer": "Apply hierarchical clustering algorithms. Compute pairwise genetic distances (substitution models). Use UPGMA or neighbor-joining to iteratively merge closest pairs into a tree. Alternatively, use maximum likelihood or Bayesian methods (more accurate but slower). Phylogeny reconstruction is a core bioinformatics problem.",
        "rubric": [
            "Identifies hierarchical clustering or phylogenetic algorithms",
            "Correctly applies to evolutionary tree reconstruction",
            "Explains pairwise distance computation (substitution models)",
            "Describes clustering algorithm (UPGMA, neighbor-joining, max likelihood)",
            "Mentions complexity tradeoffs (heuristics vs exhaustive)",
        ],
        "max_score": 5,
    },

    # Physics ↔ ML
    {
        "id": "xd040_ml_physics_hamiltonian",
        "domain_a": "Machine Learning (Sampling)",
        "domain_b": "Physics (Statistical Mechanics)",
        "problem": "You need to sample from a complex high-dimensional probability distribution (e.g., for Bayesian inference). Random walk is too slow; it takes forever to explore the space. How can you sample efficiently by exploiting gradient information?",
        "hint": "Consider physical systems that naturally explore energy landscapes.",
        "gold_answer": "Apply Hamiltonian Monte Carlo (HMC) from physics. Treat probability as energy (negative log prob). Simulate Hamiltonian dynamics (position, momentum) using gradient of energy. Momentum helps traverse low-probability regions, avoiding random walk. Leapfrog integration + Metropolis acceptance. Used in Stan, PyMC3.",
        "rubric": [
            "Identifies Hamiltonian Monte Carlo or Langevin dynamics",
            "Connects to physics (Hamiltonian, energy, momentum)",
            "Explains gradient-based proposal (not random walk)",
            "Describes momentum variable and dynamics simulation",
            "Mentions efficiency (better mixing, fewer samples needed)",
        ],
        "max_score": 5,
    },

    # Networking ↔ Security
    {
        "id": "xd041_sec_net_syn_flood",
        "domain_a": "Security (DoS Defense)",
        "domain_b": "Networking (TCP)",
        "problem": "An attacker floods a server with TCP SYN packets (spoofed source IPs). The server allocates connection state for each SYN, exhausting memory (SYN flood DoS). Dropping SYNs hurts legitimate users. How can you handle SYNs without allocating state until the connection is validated?",
        "hint": "Consider cryptographic techniques for stateless authentication.",
        "gold_answer": "Apply SYN cookies from networking security. Encode connection state (seq num, timestamp) into the ISN (initial sequence number) sent in SYN-ACK, using a cryptographic hash. Don't store state. On receiving ACK, validate and decode ISN to reconstruct state. Legitimate clients complete handshake; spoofed packets don't ACK. Defense doesn't allocate memory per SYN.",
        "rubric": [
            "Identifies SYN cookies or stateless handshake",
            "Correctly applies to DoS defense (SYN flood mitigation)",
            "Explains encoding state into ISN (cryptographic hash)",
            "Describes stateless server operation (no memory until ACK)",
            "Mentions tradeoffs (TCP options lost, CPU cost)",
        ],
        "max_score": 5,
    },

    # Compiler ↔ Security
    {
        "id": "xd042_sec_compiler_fuzzing",
        "domain_a": "Security (Vulnerability Discovery)",
        "domain_b": "Compiler Design",
        "problem": "You want to find bugs (crashes, memory errors) in a complex program. Manual testing and random inputs are ineffective (most random inputs are rejected by input validation). How can you generate inputs that reach deep into the program to trigger bugs?",
        "hint": "Consider compiler techniques for understanding program structure.",
        "gold_answer": "Apply coverage-guided fuzzing using compiler instrumentation. Compile program with code coverage tracking (LLVM SanitizerCoverage). Fuzz with mutations; prioritize inputs that increase coverage (reach new basic blocks). Feedback loop discovers deep paths. AFL, libFuzzer use this. Combines compiler analysis with evolutionary search.",
        "rubric": [
            "Identifies fuzzing with coverage guidance (AFL, libFuzzer)",
            "Connects to compiler instrumentation (coverage tracking)",
            "Explains feedback loop (mutate inputs that increase coverage)",
            "Describes evolutionary or genetic algorithm aspect",
            "Mentions effectiveness (finds bugs unreachable by random testing)",
        ],
        "max_score": 5,
    },

    # Database ↔ Biology
    {
        "id": "xd043_db_bio_blast",
        "domain_a": "Database Systems (Similarity Search)",
        "domain_b": "Biology (Genomics)",
        "problem": "A genomics database has billions of DNA sequences. A researcher submits a query sequence and wants to find similar sequences (allowing mismatches, gaps). Exact match doesn't work; comparing against every sequence is too slow. How do you search efficiently?",
        "hint": "Consider algorithmic techniques for approximate string matching at scale.",
        "gold_answer": "Apply BLAST (Basic Local Alignment Search Tool) algorithm from bioinformatics. Index database using k-mer seeds (short exact matches). For query, find k-mer hits, extend alignments using dynamic programming (Smith-Waterman). Filter by score. BLAST trades accuracy for speed using heuristics. Seminal algorithm combining indexing + DP.",
        "rubric": [
            "Identifies BLAST or seed-and-extend approach",
            "Explains k-mer indexing for initial candidate finding",
            "Describes extension phase (local alignment, DP)",
            "Mentions heuristics or filtering (E-value, score threshold)",
            "Addresses speed/accuracy tradeoff (heuristic vs optimal)",
        ],
        "max_score": 5,
    },

    # ML ↔ Game Theory
    {
        "id": "xd044_ml_game_nash",
        "domain_a": "Machine Learning (Multi-Agent RL)",
        "domain_b": "Game Theory",
        "problem": "You're training multiple RL agents that interact (e.g., poker bots, auction bidders). Each agent learns a policy. As one agent improves, others' optimal policies change (non-stationarity). How do you find a stable outcome where no agent can improve unilaterally?",
        "hint": "Consider equilibrium concepts from game theory.",
        "gold_answer": "Apply Nash equilibrium from game theory. Train agents using self-play or fictitious play until convergence to Nash: no agent can improve by changing its policy alone (best response to others). Algorithms: PSRO (Policy Space Response Oracles), CFR (Counterfactual Regret Minimization) for poker. Stable solution to multi-agent learning.",
        "rubric": [
            "Identifies Nash equilibrium from game theory",
            "Correctly applies to multi-agent RL (self-play, convergence)",
            "Explains no-unilateral-improvement property",
            "Mentions specific algorithms (CFR, PSRO, fictitious play)",
            "Addresses non-stationarity or coevolution",
        ],
        "max_score": 5,
    },

    # Economics ↔ OS
    {
        "id": "xd045_os_econ_deficit_spending",
        "domain_a": "Operating Systems (Memory Management)",
        "domain_b": "Economics (Monetary Policy)",
        "problem": "An OS needs to allocate memory to processes. If it allocates too conservatively, memory sits idle. If too aggressively, it thrashes (swap storm). How can it allocate more memory than physically available while avoiding thrashing?",
        "hint": "Consider economic concepts for managing resources that may not all be demanded simultaneously.",
        "gold_answer": "Apply overcommitment / fractional reserve banking from economics. OS allocates virtual memory exceeding physical RAM, betting not all processes use their full allocation simultaneously (like banks lending more than deposits). Monitor working sets; if physical memory fills, page out cold pages. OOM killer as last resort. Balances utilization vs risk of thrashing.",
        "rubric": [
            "Identifies overcommitment or fractional reserve concept from economics",
            "Correctly applies to memory allocation (virtual > physical)",
            "Explains assumption (not all allocations used simultaneously)",
            "Describes handling oversubscription (paging, OOM killer)",
            "Addresses utilization/risk tradeoff",
        ],
        "max_score": 5,
    },

    # Cryptography ↔ ML
    {
        "id": "xd046_ml_crypto_federated",
        "domain_a": "Machine Learning (Distributed Training)",
        "domain_b": "Cryptography",
        "problem": "Multiple hospitals want to train a shared model on patient data, but privacy laws forbid sharing raw data. How can they collaboratively train a model without any hospital seeing another's data?",
        "hint": "Consider cryptographic techniques for secure multi-party computation.",
        "gold_answer": "Apply federated learning with secure aggregation. Each hospital trains locally on its data, computes gradients. Use secure aggregation (MPC or homomorphic encryption) to sum gradients without revealing individual contributions. Central server updates global model with aggregated gradients. No raw data or individual gradients leave hospitals. Used in Google Keyboard, healthcare.",
        "rubric": [
            "Identifies federated learning or secure multi-party computation",
            "Correctly applies to distributed training with privacy",
            "Explains local training + gradient aggregation",
            "Describes secure aggregation (MPC, homomorphic encryption)",
            "Addresses privacy guarantee (no raw data or individual gradient exposure)",
        ],
        "max_score": 5,
    },

    # Networking ↔ ML
    {
        "id": "xd047_net_ml_cc",
        "domain_a": "Networking (Congestion Control)",
        "domain_b": "Machine Learning (Reinforcement Learning)",
        "problem": "TCP congestion control uses hand-tuned rules (AIMD, Cubic) that work poorly in some environments (cellular, data center). How can you automatically learn a better congestion control policy that adapts to any network?",
        "hint": "Consider ML techniques for learning control policies from trial and error.",
        "gold_answer": "Apply reinforcement learning. Model CC as MDP: state = RTT, loss, throughput; action = send rate; reward = throughput - penalty*loss. Train RL agent (DQN, PPO) via simulation or real-network trials. Learns optimal policy for diverse environments. This is PCC Vivace, Aurora, Sage. Outperforms fixed rules in heterogeneous settings.",
        "rubric": [
            "Identifies reinforcement learning or learned control",
            "Correctly applies to congestion control (MDP formulation)",
            "Explains state (network metrics), action (send rate), reward",
            "Mentions specific systems (PCC, Aurora, Sage)",
            "Addresses generalization (works across diverse networks)",
        ],
        "max_score": 5,
    },

    # Algorithms ↔ Physics
    {
        "id": "xd048_algo_physics_fft",
        "domain_a": "Algorithm Design (Signal Processing)",
        "domain_b": "Physics (Wave Analysis)",
        "problem": "You have a time-series signal (audio, vibration) and need to find the dominant frequencies. Computing the Fourier transform naively takes O(n²) time. For real-time processing (audio, radar), this is too slow. How do you compute it in O(n log n)?",
        "hint": "Consider algorithmic techniques for exploiting symmetry in computations.",
        "gold_answer": "Apply Fast Fourier Transform (FFT) algorithm. Recursively divide DFT into even/odd indices, exploit periodicity of complex exponentials (symmetry). Combine subproblems in O(n) time. Recurrence: T(n) = 2T(n/2) + O(n) → O(n log n). Cooley-Tukey FFT. Fundamental to digital signal processing, MP3, radar, MRI.",
        "rubric": [
            "Identifies FFT (Cooley-Tukey or similar)",
            "Explains divide-and-conquer exploiting periodicity/symmetry",
            "Describes recursive structure (even/odd indices)",
            "Mentions O(n log n) complexity vs O(n²) DFT",
            "Provides applications (audio, radar, MRI, spectroscopy)",
        ],
        "max_score": 5,
    },

    # OS ↔ Algorithms
    {
        "id": "xd049_os_algo_lru",
        "domain_a": "Operating Systems (Page Replacement)",
        "domain_b": "Algorithm Design (Caching)",
        "problem": "An OS has limited physical memory and must decide which pages to evict when memory fills. Evicting the wrong page causes thrashing (repeated page faults). Optimal eviction requires knowing the future (impossible). How do you approximate optimal eviction using only past access history?",
        "hint": "Consider algorithmic heuristics based on temporal locality.",
        "gold_answer": "Apply Least Recently Used (LRU) cache replacement. Evict the page accessed longest ago, betting it's least likely to be accessed soon (temporal locality). Approximates optimal (Belady's algorithm). Implemented efficiently with a doubly-linked list + hash table, or clock algorithm (hardware approximation). Fundamental to OS paging and CPU caches.",
        "rubric": [
            "Identifies LRU or cache replacement policy",
            "Correctly applies to page replacement in OS",
            "Explains temporal locality assumption (recent = likely to recur)",
            "Describes implementation (list, clock algorithm)",
            "Mentions alternatives (LFU, MRU) or Belady's optimum",
        ],
        "max_score": 5,
    },

    # Security ↔ Economics
    {
        "id": "xd050_sec_econ_proof_of_work",
        "domain_a": "Security (Sybil Attack Defense)",
        "domain_b": "Economics (Resource Scarcity)",
        "problem": "A decentralized system (blockchain, spam filter) needs to prevent Sybil attacks (one attacker creating many fake identities). Centralized identity verification isn't available. How can you make creating identities expensive enough to deter mass attacks?",
        "hint": "Consider economic mechanisms that tie digital actions to costly real-world resources.",
        "gold_answer": "Apply proof-of-work from economics / Hashcash. Require each identity to solve a computational puzzle (e.g., find hash with k leading zeros). Puzzle is expensive (CPU, energy) but easy to verify. Cost scales linearly with identities, making mass Sybil attacks economically infeasible. Used in Bitcoin, spam filters (Hashcash), DOS mitigation.",
        "rubric": [
            "Identifies proof-of-work or proof-of-resource mechanism",
            "Correctly applies to Sybil attack defense",
            "Explains computational cost (hash puzzle, energy/CPU expense)",
            "Describes verification (cheap to check proof)",
            "Mentions economic deterrent (cost scales with attack size)",
        ],
        "max_score": 5,
    },
]


@register("xdomain")
def load_xdomain(n: int = 50, seed: int = 42) -> list[Question]:
    """Load XDomain cross-domain synthesis benchmark.

    Args:
        n: Number of problems (max 50)
        seed: Random seed for deterministic subsampling

    Returns:
        List of Question objects
    """
    if n > len(PROBLEMS):
        raise ValueError(f"Only {len(PROBLEMS)} problems available, requested {n}")

    rng = random.Random(seed)
    selected = rng.sample(PROBLEMS, n)

    questions = []
    for prob in selected:
        # Format the question with domain context
        question_text = f"""**Domain: {prob['domain_a']}**

{prob['problem']}

**Hint**: {prob['hint']}

Provide a detailed solution that:
1. Identifies the relevant concept/technique from {prob['domain_b']}
2. Explains how to apply it to solve the problem in {prob['domain_a']}
3. Discusses any tradeoffs or implementation considerations
"""

        # Store rubric and gold answer in metadata for judge evaluation
        questions.append(Question(
            id=prob["id"],
            dataset="xdomain",
            subject=f"{prob['domain_a']} + {prob['domain_b']}",
            question=question_text,
            choices=None,  # Open-ended, not multiple choice
            answer_key="",  # Will be scored by LLM judge, not exact match
            metadata={
                "domain_a": prob["domain_a"],
                "domain_b": prob["domain_b"],
                "gold_answer": prob["gold_answer"],
                "rubric": prob["rubric"],
                "max_score": prob["max_score"],
            }
        ))

    return questions


# XDomain V4: Harder cross-domain problems (20 problems)
XDOMAIN_V4_PROBLEMS: list[XDomainProblem] = [
    # Quantum Computing ↔ Cryptography
    {
        "id": "xdv4_001_crypto_quantum_qkd",
        "domain_a": "Cryptography (Key Exchange)",
        "domain_b": "Quantum Computing (Quantum Mechanics)",
        "problem": "Classical key exchange (Diffie-Hellman) is vulnerable to quantum computers running Shor's algorithm. You need a key exchange protocol that remains secure even against quantum attackers with unlimited computational power. How can you achieve information-theoretic security?",
        "hint": "Consider properties of quantum measurement and the no-cloning theorem.",
        "gold_answer": "Apply Quantum Key Distribution (QKD), specifically BB84 protocol. Encode bits in quantum states (photon polarizations). Eavesdropping requires measurement, which disturbs quantum states (no-cloning theorem, measurement collapse). Alice and Bob detect disturbance, abort if eavesdropping suspected. Security is physics-based, not computational. Achieves information-theoretic security against any adversary.",
        "rubric": [
            "Identifies QKD or BB84 protocol from quantum mechanics",
            "Explains quantum state encoding (polarization, basis)",
            "Describes eavesdropping detection via state disturbance",
            "Mentions no-cloning theorem or measurement collapse",
            "Addresses information-theoretic security vs computational",
        ],
        "max_score": 5,
    },

    # Category Theory ↔ Distributed Systems
    {
        "id": "xdv4_002_dist_category_crdt",
        "domain_a": "Distributed Systems (Eventual Consistency)",
        "domain_b": "Category Theory (Abstract Algebra)",
        "problem": "Multiple replicas of a data structure accept concurrent updates without coordination. Updates propagate asynchronously. You need the replicas to eventually converge to the same state despite arbitrary message delays and reordering. Traditional merge strategies (last-write-wins) lose data. How do you guarantee convergence while preserving all updates?",
        "hint": "Consider algebraic structures where order of operations doesn't matter.",
        "gold_answer": "Apply Conflict-Free Replicated Data Types (CRDTs) based on semilattices from abstract algebra. Design data structure as a join-semilattice where merge operation is commutative, associative, and idempotent (mathematical lattice). Any sequence of merges converges to same state (Strong Eventual Consistency). Examples: G-Counter (increment-only), OR-Set (add-wins). Category theory's functoriality ensures composition.",
        "rubric": [
            "Identifies CRDTs or semilattice structure from abstract algebra",
            "Explains commutativity, associativity, idempotence properties",
            "Describes convergence guarantee (Strong Eventual Consistency)",
            "Provides concrete CRDT examples (G-Counter, OR-Set, LWW-Register)",
            "Mentions lattice theory or join operation",
        ],
        "max_score": 5,
    },

    # Stochastic Calculus ↔ Reinforcement Learning
    {
        "id": "xdv4_003_rl_stochastic_policy_gradient",
        "domain_a": "Reinforcement Learning (Policy Optimization)",
        "domain_b": "Stochastic Calculus (Finance)",
        "problem": "An RL agent's policy is parameterized by neural network weights θ. You want to compute the gradient of expected return with respect to θ to improve the policy. But the return is stochastic (depends on random environment transitions and policy actions). Taking gradients through stochastic sampling is not directly possible. How do you compute an unbiased gradient estimator?",
        "hint": "Consider techniques for differentiating expectations in financial derivatives pricing.",
        "gold_answer": "Apply the log-derivative trick (REINFORCE, score function estimator) from stochastic calculus. Key insight: ∇_θ E[f] = E[f ∇_θ log p_θ]. Rewrite gradient of expectation as expectation of gradient. Sample trajectories, weight gradient by return (f = cumulative reward). Unbiased but high variance. Variance reduction: baselines (value function), advantage estimation (A3C, PPO). Foundation of policy gradient methods.",
        "rubric": [
            "Identifies log-derivative trick or score function estimator",
            "Explains ∇_θ E[f] = E[f ∇_θ log p_θ] transformation",
            "Describes unbiased gradient estimation via sampling",
            "Mentions high variance and variance reduction (baseline, advantage)",
            "Connects to policy gradient methods (REINFORCE, A3C, PPO)",
        ],
        "max_score": 5,
    },

    # Formal Verification ↔ Contract Law
    {
        "id": "xdv4_004_law_formal_smart_contracts",
        "domain_a": "Contract Law (Legal Agreements)",
        "domain_b": "Formal Verification (Program Correctness)",
        "problem": "Smart contracts on blockchains execute automatically but cannot be amended after deployment. A bug in a smart contract handling $100M can cause catastrophic losses (e.g., DAO hack). Traditional contract law allows renegotiation and court interpretation. How can you provide similar safety guarantees for immutable code?",
        "hint": "Consider techniques for mathematically proving software correctness.",
        "gold_answer": "Apply formal verification methods from program correctness. Specify contract behavior as formal logic (pre/post-conditions, invariants) using temporal logic or Hoare logic. Use theorem provers (Coq, Isabelle) or model checkers to mechanically verify code satisfies specification. Tools like Certora, K Framework verify Solidity. Combines type theory (dependent types) with verification. Provides mathematical proof of correctness, analogous to legal contract interpretation.",
        "rubric": [
            "Identifies formal verification or theorem proving",
            "Explains formal specifications (pre/post-conditions, invariants)",
            "Describes mechanical verification (theorem prover, model checker)",
            "Mentions smart contract verification tools (Certora, K, Mythril)",
            "Addresses immutability vs traditional contract amendment",
        ],
        "max_score": 5,
    },

    # Topology ↔ Sensor Networks
    {
        "id": "xdv4_005_sensor_topology_coverage",
        "domain_a": "Sensor Networks (Coverage)",
        "domain_b": "Topology (Algebraic Topology)",
        "problem": "You deploy 1000 sensors with limited range in a field. Each sensor covers a disk. You need to verify that the entire field is covered (no holes) using only local connectivity information (which sensors can communicate). Global position information is unavailable. How do you detect coverage holes using only neighbor relationships?",
        "hint": "Consider mathematical frameworks for studying shapes and connectivity.",
        "gold_answer": "Apply persistent homology from algebraic topology. Build Rips complex from sensor connectivity graph (nodes = sensors, edges = can communicate). Compute homology groups (H_0 = components, H_1 = holes, H_2 = voids). Birth-death diagrams (persistence) identify true holes vs noise. Coverage hole = persistent 1-cycle. This is computational topology applied to distributed sensing. No global coordinates needed, only local connectivity.",
        "rubric": [
            "Identifies persistent homology or algebraic topology",
            "Explains Rips/Cech complex construction from connectivity",
            "Describes homology groups (H_0, H_1, H_2) for hole detection",
            "Mentions persistence diagrams or birth-death tracking",
            "Addresses coordinate-free, distributed computation",
        ],
        "max_score": 5,
    },

    # Information Geometry ↔ Machine Learning
    {
        "id": "xdv4_006_ml_info_geometry_natural_gradient",
        "domain_a": "Machine Learning (Optimization)",
        "domain_b": "Information Geometry (Differential Geometry)",
        "problem": "Standard gradient descent in neural network parameter space treats all directions equally (Euclidean geometry). But different directions have vastly different effects on the output distribution (some parameters are more sensitive). This causes slow convergence and requires careful learning rate tuning. How can you account for the geometry of the probability distribution space?",
        "hint": "Consider differential geometry of probability distributions.",
        "gold_answer": "Apply natural gradient descent from information geometry. Use Fisher Information Matrix (FIM) as Riemannian metric on probability manifold. Natural gradient = F^{-1} ∇θ follows steepest descent in distribution space (KL divergence), not parameter space. Invariant to reparameterization. Faster convergence. K-FAC approximates FIM efficiently. Foundation of modern optimizers (Adam approximates diagonal natural gradient).",
        "rubric": [
            "Identifies natural gradient or information geometry",
            "Explains Fisher Information Matrix as Riemannian metric",
            "Describes distribution space (manifold of probabilities)",
            "Mentions KL divergence or invariance to reparameterization",
            "Addresses computational approximations (K-FAC, Adam)",
        ],
        "max_score": 5,
    },

    # (Continue with remaining 14 problems - truncated for brevity in this edit)
    # ... problems 007-020 would be added here ...
]


@register("xdomain_v4")
def load_xdomain_v4(n: int = 20, seed: int = 42) -> list[Question]:
    """Load XDomain V4 - harder cross-domain synthesis benchmark.

    Args:
        n: Number of problems (max 20)
        seed: Random seed for deterministic subsampling

    Returns:
        List of Question objects
    """
    if n > len(XDOMAIN_V4_PROBLEMS):
        raise ValueError(f"Only {len(XDOMAIN_V4_PROBLEMS)} problems available, requested {n}")

    rng = random.Random(seed)
    selected = rng.sample(XDOMAIN_V4_PROBLEMS, n)

    questions = []
    for prob in selected:
        # Format the question with domain context
        question_text = f"""**Domain: {prob['domain_a']}**

{prob['problem']}

**Hint**: {prob['hint']}

Provide a detailed solution that:
1. Identifies the relevant concept/technique from {prob['domain_b']}
2. Explains how to apply it to solve the problem in {prob['domain_a']}
3. Discusses any tradeoffs or implementation considerations
"""

        # Store rubric and gold answer in metadata for judge evaluation
        questions.append(Question(
            id=prob["id"],
            dataset="xdomain_v4",
            subject=f"{prob['domain_a']} + {prob['domain_b']}",
            question=question_text,
            choices=None,  # Open-ended, not multiple choice
            answer_key="",  # Will be scored by LLM judge, not exact match
            metadata={
                "domain_a": prob["domain_a"],
                "domain_b": prob["domain_b"],
                "gold_answer": prob["gold_answer"],
                "rubric": prob["rubric"],
                "max_score": prob["max_score"],
            }
        ))

    return questions

# XDomain V4: Harder cross-domain problems - MULTIPLE CHOICE VERSION (iter266 redesign)
# Converted from rubric-grading to MC to fix grading bug from iter265
XDOMAIN_V4_PROBLEMS: list[XDomainProblem] = [
    {
        "id": "xdv4_001_crypto_quantum_qkd",
        "domain_a": "Cryptography (Key Exchange)",
        "domain_b": "Quantum Computing (Quantum Mechanics)",
        "problem": """Classical key exchange (Diffie-Hellman) is vulnerable to quantum computers running Shor's algorithm. You need a key exchange protocol that remains secure even against quantum attackers with unlimited computational power. How can you achieve information-theoretic security?""",
        "hint": "Consider properties of quantum measurement and the no-cloning theorem.",
        "choices": {
            "A": "Apply quantum teleportation-based key distribution. Use entangled EPR pairs to teleport key bits. Eavesdropping detected via Bell inequality violation (CHSH test). Provides information-theoretic security but requires pre-shared entangled states and quantum memory at both endpoints.",
            "B": "Apply Quantum Key Distribution (QKD) using BB84 protocol. Encode bits in photon polarizations. Eavesdropping disturbs quantum states due to no-cloning theorem and measurement collapse, enabling detection. Provides information-theoretic security, not computational.",
            "C": "Use continuous-variable QKD (CV-QKD) with coherent states and homodyne detection. Security based on entropic uncertainty principle in phase space. Easier to implement with standard telecom equipment than discrete BB84, but lower key rate and range due to Gaussian channel capacity limits.",
            "D": "Apply quantum coin-flipping (e.g., Mayers' protocol) extended to key exchange. Two parties commit to quantum states, reveal in rounds. Cheating detected via conjugate basis measurements. Provides weak coin-flipping with bounded cheating probability, not full key exchange.",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Uses quantum (EPR, Bell) but wrong protocol - teleportation is for state transfer not key distribution, requires pre-shared entanglement",
            "C": "Right category (QKD) but wrong variant - CV-QKD has different security basis and practical tradeoffs",
            "D": "Related quantum crypto primitive but wrong application - coin-flipping ≠ key exchange",
        },
    },

    {
        "id": "xdv4_002_dist_category_crdt",
        "domain_a": "Distributed Systems (Eventual Consistency)",
        "domain_b": "Category Theory (Abstract Algebra)",
        "problem": """Multiple replicas of a data structure accept concurrent updates without coordination. Updates propagate asynchronously. You need the replicas to eventually converge to the same state despite arbitrary message delays and reordering. Traditional merge strategies (last-write-wins) lose data. How do you guarantee convergence while preserving all updates?""",
        "hint": "Consider algebraic structures where order of operations doesn't matter.",
        "choices": {
            "A": "Apply operational transformation (OT) from category theory of string monoids. Transform concurrent ops via inclusion-transformation (IT): for concurrent ops a||b, compute a' = IT(a, b), b' = IT(b, a) such that a;b' = b;a'. Requires transformation functions satisfy TP1/TP2 properties. Used in collaborative editing but complex convergence proofs.",
            "B": "Apply Conflict-Free Replicated Data Types (CRDTs) based on semilattices. Design merge as commutative, associative, and idempotent join operation. Any merge sequence converges (Strong Eventual Consistency). Examples: G-Counter, OR-Set.",
            "C": "Use version vectors with causal ordering from lattice theory. Track causal dependencies via vector clocks, defer conflicting updates until causally ready. Apply LUB (least upper bound) merge for concurrent branches. Guarantees causal consistency but not convergence without resolution policy.",
            "D": "Apply free group constructions from abstract algebra. Model updates as group elements, concurrent updates as commutators. Merge by reducing to normal form via rewrite rules. Convergence when rewrite system is confluent (Church-Rosser property).",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Related (OT) but harder to prove convergence, doesn't explicitly use semilattice structure",
            "C": "Uses partial orders but lacks join semilattice property for guaranteed convergence",
            "D": "Mentions algebra but wrong algebraic structure - groups not semilattices, rewriting ≠ CRDTs",
        },
    },

    {
        "id": "xdv4_003_rl_stochastic_policy_gradient",
        "domain_a": "Reinforcement Learning (Policy Optimization)",
        "domain_b": "Stochastic Calculus (Finance)",
        "problem": """An RL agent's policy is parameterized by neural network weights θ. You want to compute the gradient of expected return with respect to θ to improve the policy. But the return is stochastic (depends on random environment transitions and policy actions). Taking gradients through stochastic sampling is not directly possible. How do you compute an unbiased gradient estimator?""",
        "hint": "Consider techniques for differentiating expectations in financial derivatives pricing.",
        "choices": {
            "A": "Apply Malliavin calculus (stochastic variational calculus) to differentiate through the probability measure. Use integration by parts in function space: ∇_θ E[f(X_θ)] = E[f(X_θ) · H_θ] where H_θ is Malliavin weight. Requires smoothness of p_θ. Lower variance than REINFORCE but harder to implement.",
            "B": "Apply log-derivative trick (REINFORCE, score function estimator) from stochastic calculus: ∇_θ E[f] = E[f ∇_θ log p_θ]. Sample trajectories, weight by return. Unbiased but high-variance. Use baselines (value function, advantage) for variance reduction.",
            "C": "Use reparameterization trick combined with Girsanov theorem. Rewrite sampling as deterministic function θ plus Wiener process increment. Apply change-of-measure via Radon-Nikodym derivative. Enables pathwise gradients for diffusion policies. Low variance but requires differentiable sampling.",
            "D": "Apply Stein's lemma from probability theory: for X ~ p_θ, if Ep[f(X)∇log p_θ] = 0, then ∇_θ E[f] can be computed without evaluating f∇log p. Requires control variates from Stein operator. Used in Stein variational gradient descent but indirect for policy gradients.",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Advanced stochastic calculus but Malliavin is for anticipating processes, overkill/wrong tool for RL",
            "C": "Mentions Girsanov (stochastic calc) but reparam+Girsanov combo is non-standard, Girsanov for measure change not gradients",
            "D": "Stein's lemma is real but applied wrongly - it's for score matching not policy gradients",
        },
    },

    {
        "id": "xdv4_004_law_formal_smart_contracts",
        "domain_a": "Contract Law (Legal Agreements)",
        "domain_b": "Formal Verification (Program Correctness)",
        "problem": """Smart contracts on blockchains execute automatically but cannot be amended after deployment. A bug in a smart contract handling $100M can cause catastrophic losses (e.g., DAO hack). Traditional contract law allows renegotiation and court interpretation. How can you provide similar safety guarantees for immutable code?""",
        "hint": "Consider techniques for mathematically proving software correctness.",
        "choices": {
            "A": "Apply runtime verification with monitors that check invariants during execution. Embed assertion checks (assert(balance >= 0)) compiled to opcodes. Failed assertions revert transaction. Provides runtime guarantees but not exhaustive pre-deployment proof.",
            "B": "Apply formal verification using theorem provers (Coq, Isabelle) or model checkers. Specify behavior as formal logic (pre/post-conditions, invariants). Tools like Certora and K Framework mechanically verify Solidity satisfies specs, providing mathematical correctness proof.",
            "C": "Use bounded model checking (BMC) with SMT solvers. Encode contract state transitions as SMT formulas, check property violations up to depth k. Tools like Manticore explore execution paths symbolically. Finds bugs within bounds but incomplete (doesn't prove absence of bugs beyond bound k).",
            "D": "Apply abstract interpretation to overapproximate contract behavior. Build abstract domain (intervals, octagons) capturing possible states. If abstract execution satisfies property, concrete execution does too. Sound but may produce false positives. Used in tools like Securify.",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Runtime verification is a verification technique but checks at runtime (post-deployment), doesn't prevent bugs",
            "C": "BMC is formal verification but bounded/incomplete, doesn't provide full correctness proof",
            "D": "Abstract interpretation is formal method but imprecise (false positives), not complete proof",
        },
    },

    {
        "id": "xdv4_005_sensor_topology_coverage",
        "domain_a": "Sensor Networks (Coverage)",
        "domain_b": "Topology (Algebraic Topology)",
        "problem": """You deploy 1000 sensors with limited range in a field. Each sensor covers a disk. You need to verify that the entire field is covered (no holes) using only local connectivity information (which sensors can communicate). Global position information is unavailable. How do you detect coverage holes using only neighbor relationships?""",
        "hint": "Consider mathematical frameworks for studying shapes and connectivity.",
        "choices": {
            "A": "Apply Čech cohomology from algebraic topology. Build Čech complex where k-simplices = (k+1) sensors with pairwise overlapping coverage disks. Compute cohomology groups via cochain complex. H^1 ≠ 0 indicates holes. Čech more geometrically accurate than Rips but computationally expensive (exponential in clique size).",
            "B": "Apply persistent homology from algebraic topology. Build Rips complex from connectivity graph. Compute homology groups (H_0=components, H_1=holes, H_2=voids). Persistence diagrams identify true holes vs noise. Holes = persistent 1-cycles. Coordinate-free.",
            "C": "Use nerve theorem from combinatorial topology. Build nerve complex N(U) where U = sensor coverage disks, k-simplices = (k+1)-fold intersections. If coverage regions convex, nerve is homotopy-equivalent to union. Compute H_1 of nerve for holes. Requires known positions to compute intersections.",
            "D": "Apply de Rham cohomology on the configuration manifold of sensor positions. Coverage holes correspond to non-trivial cohomology classes. Use Hodge decomposition: harmonic forms ≡ cohomology generators. Requires smooth manifold structure and global coordinates.",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Čech is valid topology approach but requires geometric intersection info (violates local-only constraint), more complex than Rips",
            "C": "Nerve theorem is correct topology but requires position information to compute intersections",
            "D": "de Rham cohomology is smooth version, requires coordinates and differentiable structure, overkill for discrete sensor graph",
        },
    },

    {
        "id": "xdv4_006_ml_info_geometry_natural_gradient",
        "domain_a": "Machine Learning (Optimization)",
        "domain_b": "Information Geometry (Differential Geometry)",
        "problem": """Standard gradient descent in neural network parameter space treats all directions equally (Euclidean geometry). But different directions have vastly different effects on the output distribution (some parameters are more sensitive). This causes slow convergence and requires careful learning rate tuning. How can you account for the geometry of the probability distribution space?""",
        "hint": "Consider differential geometry of probability distributions.",
        "choices": {
            "A": "Apply Riemannian gradient descent using the pull-back metric from output manifold. Treat output distribution as point on probability simplex (Riemannian manifold with Fisher-Rao metric). Pull metric back to parameter space via network Jacobian. More principled than Euclidean but requires Jacobian computation at each step.",
            "B": "Apply natural gradient descent from information geometry. Use Fisher Information Matrix as Riemannian metric on probability manifold. Natural gradient = F^{-1} ∇θ follows steepest descent in distribution space (KL), not parameter space. K-FAC approximates efficiently.",
            "C": "Use Hessian-based second-order methods (Newton, quasi-Newton/L-BFGS). Hessian ∇²L captures local curvature in parameter space (not distribution space). Preconditions gradient by H^{-1}. Faster convergence but Hessian ≠ Fisher (different metrics: parameter curvature vs distribution geometry).",
            "D": "Apply mirror descent from convex optimization. Use Bregman divergence induced by log-sum-exp. Update in dual space, project back. For neural nets, corresponds to entropy-regularized gradient descent. Different geometry than Fisher metric (dual Bregman vs primal-dual Fisher).",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Mentions Riemannian/Fisher-Rao but wrong application (pullback via Jacobian not same as Fisher Information Matrix)",
            "C": "Second-order but Hessian of loss ≠ Fisher metric; confuses parameter-space curvature with distribution-space geometry",
            "D": "Mirror descent uses different geometry (Bregman not Fisher), not same as natural gradient",
        },
    },

        {
        "id": "xdv4_007_signal_dp_private_spectrum",
        "domain_a": "Signal Processing (Spectrum Estimation)",
        "domain_b": "Differential Privacy (Privacy-Preserving Statistics)",
        "problem": """A cellular network wants to publish aggregate spectrum usage data (which frequencies are occupied) to optimize allocation. But individual device usage patterns are sensitive (can reveal location, identity). Simply aggregating devices isn't sufficient—statistical attacks can still infer individual behavior. How do you publish spectrum statistics while provably protecting individual privacy?""",
        "hint": "Consider privacy frameworks that bound information leakage.",
        "choices": {
            "A": """Apply randomized response from survey sampling. Each device flips coin: report true frequency with prob p, random frequency with prob (1-p). Aggregated statistics unbiased: E[reported] = p·true + (1-p)·uniform. Provides plausible deniability but weaker than differential privacy (no composition theorem, bounded adversary).""",
            "B": """Apply secure aggregation (multi-party computation). Devices mask measurements with secret shares, aggregator sums without seeing individuals. Cryptographic MPC hides individual data but provides NO privacy guarantee if aggregator colludes or aggregates leak via statistical attacks. No formal privacy bound.""",
            "C": """Apply differential privacy by adding calibrated Laplace/Gaussian noise to Fourier coefficients or bins. Noise scale ∝ 1/ε (privacy parameter). Composition theorem tracks privacy loss. Provides formal bounded leakage guarantee with accuracy/privacy tradeoff.""",
            "D": """Apply local differential privacy (LDP) where each device adds noise BEFORE sending data. Each measurement perturbed independently via randomized algorithm (e.g., RAPPOR for categorical, Gaussian for numerical). Aggregator never sees raw data. Stronger trust model than central DP but higher noise (√n penalty).""",
        },
        "answer_key": "C",
        "distractor_analysis": {
            "A": "Randomized response is privacy technique but pre-DP, lacks formal composition and bounded guarantee",
            "B": "Secure aggregation is crypto not privacy - hides data in transit but doesn't bound statistical inference",
            "D": "LDP is variant of DP but local not central - different trust model, higher noise, question implies central setting",
        },
    },

        {
        "id": "xdv4_008_epidemic_control_sir",
        "domain_a": "Epidemiology (Disease Control)",
        "domain_b": "Control Theory (Dynamical Systems)",
        "problem": """A pandemic is spreading. You have limited intervention resources (vaccines, lockdowns) that are costly. Intervening too early wastes resources; too late causes mass casualties. You need a control policy that minimizes total cost (infections + intervention) under uncertainty about disease parameters. How do you design an optimal intervention strategy?""",
        "hint": "Consider frameworks for optimal control of dynamical systems.",
        "choices": {
            "A": """Apply stochastic optimal control using dynamic programming on Markov chain. Discretize SIR states, model transitions as stochastic (binomial infection events). Solve Bellman equation backward from terminal time. Policy = optimal action per state. Captures stochasticity but intractable for large populations (curse of dimensionality).""",
            "B": """Apply optimal control theory to SIR/SEIR models. Formulate as Hamilton-Jacobi-Bellman: minimize cost J = ∫(cI + uR)dt subject to SIR dynamics. Use Pontryagin's maximum principle or Model Predictive Control for online adaptation under uncertainty.""",
            "C": """Apply Lyapunov-based control from stability theory. Design Lyapunov function V(S,I,R) (e.g., V = I²). Choose control u to make dV/dt < 0, driving I → 0. Guarantees stability but doesn't minimize cost J, may be overly aggressive or conservative.""",
            "D": """Apply Linear Quadratic Gaussian (LQG) control by linearizing SIR around equilibrium. Design LQR for deterministic linearized system, add Kalman filter for state estimation under noise. Optimal for linear-Gaussian but SIR is nonlinear (linearization valid only near equilibrium).""",
        },
        "answer_key": "B",
        "distractor_analysis": {
            "A": "Stochastic DP is correct category but intractable, question asks for 'optimal control' which typically means continuous HJB/Pontryagin",
            "C": "Lyapunov control stabilizes but doesn't optimize cost, different objective than minimizing J",
            "D": "LQG applies to linear systems, SIR is nonlinear - linearization breaks far from equilibrium",
        },
    },

        {
        "id": "xdv4_009_dl_rep_theory_equivariance",
        "domain_a": "Deep Learning (Computer Vision)",
        "domain_b": "Representation Theory (Group Theory)",
        "problem": """A convolutional neural network for object detection is trained on upright images. At test time, images may be rotated. Augmenting training data with rotations is expensive and doesn't generalize to continuous rotations. How can you build rotation equivariance directly into the network architecture so learned features automatically adapt to arbitrary rotations?""",
        "hint": "Consider mathematical structures for encoding symmetries.",
        "choices": {
            "A": """Apply group equivariant CNNs using representation theory. Replace convolution with G-convolution over rotation group SO(2) or C_n. Filters transform per group representations. Network outputs transform predictably under rotation (equivariance). Steerable CNNs use harmonic analysis.""",
            "B": """Use spatial transformer networks (STN). Add learnable module that predicts and applies geometric transformation to input. Network learns to normalize rotations before processing, achieving rotation invariance.""",
            "C": """Apply rotation data augmentation at train time, but also add rotation angle as auxiliary input at test time. Network learns to condition on angle, handling arbitrary rotations.""",
            "D": """Use capsule networks with dynamic routing. Capsules encode entity pose (position, rotation) explicitly. Routing by agreement clusters rotated instances, achieving rotation equivariance.""",
        },
        "answer_key": "A",
    },

        {
        "id": "xdv4_010_os_queueing_io_scheduler",
        "domain_a": "Operating Systems (I/O Scheduling)",
        "domain_b": "Queueing Theory (Operations Research)",
        "problem": """A disk serves requests with varying seek times. FCFS (first-come-first-served) is fair but slow. Shortest-Seek-Time-First (SSTF) minimizes latency but can starve distant requests indefinitely. You need a scheduler that balances throughput and fairness while providing bounded latency guarantees. How do you analyze and design such a scheduler?""",
        "hint": "Consider mathematical frameworks for analyzing service systems.",
        "choices": {
            "A": """Use Completely Fair Scheduler (CFS) approach from CPU scheduling. Track virtual runtime for each request, always service request with minimum runtime. Guarantees fairness with O(log n) overhead.""",
            "B": """Apply queueing theory: model as M/G/1 with priority + aging. Use SCAN/C-SCAN base. Add age-based priority (waiting time boosts priority) to prevent starvation. Analyze via Pollaczek-Khinchin. BFQ provides fairness+latency bounds.""",
            "C": """Implement deficit round robin. Assign each request queue a quantum (time budget). Service requests round-robin, decrement quantum by seek time. Unused quantum carries over, ensuring fairness.""",
            "D": """Use machine learning to predict request patterns. Train model on historical workloads, predict future arrival patterns, preemptively reorder queue to minimize expected latency.""",
        },
        "answer_key": "B",
    },

        {
        "id": "xdv4_011_ml_statmech_boltzmann",
        "domain_a": "Machine Learning (Generative Models)",
        "domain_b": "Statistical Mechanics (Physics)",
        "problem": """You want to model the joint distribution of many correlated binary variables (e.g., pixels, user preferences). Specifying P(x) directly requires enumerating 2^n states (intractable). You need a model that captures complex dependencies but remains tractable for inference and learning. How do you design such a model?""",
        "hint": "Consider physical systems with many interacting particles.",
        "choices": {
            "A": """Use autoregressive models like PixelCNN. Factor P(x) = ∏P(x_i|x_{<i}). Model each conditional with neural network. Captures dependencies without intractable partition function.""",
            "B": """Apply variational autoencoders (VAE). Encode to latent z, decode to reconstruct x. Optimize ELBO = E[log p(x|z)] - KL(q(z|x)||p(z)). Tractable approximate inference.""",
            "C": """Apply Boltzmann machines from statistical mechanics: P(x) = exp(-E(x))/Z, E(x) = -Σw_ij x_i x_j (Ising model). Learn via contrastive divergence (approximate ∇log-likelihood). Restricted Boltzmann Machines enable tractable inference.""",
            "D": """Use normalizing flows. Apply invertible transformations f: x → z where z ~ N(0,I). P(x) = P(f(x))|det(∂f/∂x)|. Tractable likelihood via change-of-variables formula.""",
        },
        "answer_key": "C",
    },

        {
        "id": "xdv4_012_coding_alg_geom_rs",
        "domain_a": "Error Correction (Communication)",
        "domain_b": "Algebraic Geometry (Pure Mathematics)",
        "problem": """A communication channel corrupts data. You want to encode messages so they can be decoded even after errors. Simple repetition codes are inefficient (3x overhead for 1-bit error correction). You need codes that correct many errors with minimal overhead. How do you design codes with optimal tradeoff between rate and error correction?""",
        "hint": "Consider polynomial interpolation and evaluation over finite fields.",
        "choices": {
            "A": """Apply Reed-Solomon codes from algebraic geometry. Encode message as polynomial coefficients over finite field, evaluate at n points. Correct up to (n-k)/2 errors via polynomial interpolation (Berlekamp-Welch). Optimal rate-distance tradeoff (MDS).""",
            "B": """Use LDPC (Low-Density Parity-Check) codes. Sparse parity-check matrix enables efficient belief propagation decoding. Near Shannon capacity with iterative message passing.""",
            "C": """Apply turbo codes with parallel concatenated convolutional encoders. Iteratively decode using BCJR algorithm, exchanging extrinsic information. Achieves near-capacity performance.""",
            "D": """Use fountain codes (LT, Raptor). Generate infinite stream of encoded symbols from message. Receiver collects any k' ≈ k symbols to decode. Rateless property handles unknown erasure rates.""",
        },
        "answer_key": "A",
    },

        {
        "id": "xdv4_013_stream_measure_distinct",
        "domain_a": "Streaming Algorithms (Big Data)",
        "domain_b": "Measure Theory (Probability)",
        "problem": """A stream of billions of user IDs arrives (e.g., web traffic). You need to estimate the number of distinct users in real-time using only a few kilobytes of memory (can't store all IDs). Exact counting requires O(n) space. How do you estimate cardinality (distinct count) in O(log n) space with provable accuracy bounds?""",
        "hint": "Consider probabilistic techniques for approximating set properties.",
        "choices": {
            "A": """Use Bloom filter with multiple hash functions. Each ID hashed to multiple bits. Estimate cardinality from fraction of zero bits remaining. Space-efficient probabilistic structure.""",
            "B": """Apply HyperLogLog from probabilistic counting and extreme value theory. Hash IDs to [0,1], track minimum (or leading zeros). Min ≈ 1/n. Use harmonic mean over m estimators to reduce variance. σ ≈ 1.04/√m with O(m log log n) bits.""",
            "C": """Sample IDs with probability p = 1000/n (estimate n first). Store sampled set S. Estimate cardinality as |S|/p. Reservoir sampling maintains uniform sample without knowing n ahead.""",
            "D": """Use count-min sketch. Maintain d×w array of counters. Hash each ID to d positions, increment counters. Estimate count as minimum across d arrays. Handles updates and queries efficiently.""",
        },
        "answer_key": "B",
    },

        {
        "id": "xdv4_014_crypto_lattice_post_quantum",
        "domain_a": "Cryptography (Public-Key Encryption)",
        "domain_b": "Number Theory (Lattice Theory)",
        "problem": """RSA and elliptic curve cryptography will be broken by quantum computers (Shor's algorithm). You need post-quantum public-key encryption that resists quantum attacks. The security must reduce to a mathematically hard problem believed to be quantum-resistant. What mathematical structure provides such hardness?""",
        "hint": "Consider geometric problems in high-dimensional spaces.",
        "choices": {
            "A": """Use hash-based signatures like SPHINCS+. Security reduces to collision-resistant hash functions, believed quantum-resistant. Stateless signatures avoid key management issues of Lamport/Merkle schemes.""",
            "B": """Apply code-based cryptography like McEliece. Security reduces to decoding random linear codes (NP-hard). Large key sizes but fast encryption. Quantum resistance believed strong.""",
            "C": """Apply lattice-based cryptography (LWE - Learning With Errors). Security reduces to Shortest Vector Problem, believed quantum-hard. Encrypt by adding noise to lattice point. Kyber (NIST standard), NTRU use polynomial rings. Worst-case to average-case reduction.""",
            "D": """Use isogeny-based cryptography like SIDH. Security based on finding isogenies between elliptic curves. Compact keys, but recent attacks (Castryck-Decru) raise concerns about SIDH specifically.""",
        },
        "answer_key": "C",
    },

        {
        "id": "xdv4_015_sensor_sheaf_consistency",
        "domain_a": "Sensor Fusion (Multi-Sensor Systems)",
        "domain_b": "Sheaf Theory (Category Theory)",
        "problem": """You have multiple sensors (cameras, radar, lidar) observing an environment. Each sensor provides partial, overlapping information with inconsistencies (noise, calibration errors). You need to fuse sensor data into a globally consistent estimate while detecting and localizing inconsistencies. Simple averaging fails when some sensors malfunction. How do you reason about consistency across overlapping sensor views?""",
        "hint": "Consider mathematical frameworks for gluing local data into global structures.",
        "choices": {
            "A": """Use Kalman filtering with sensor fusion equations. Each sensor provides measurement with noise covariance. Kalman filter optimally combines measurements weighted by uncertainty, producing fused estimate.""",
            "B": """Apply Bayesian inference. Model each sensor as likelihood P(observation|state). Combine via Bayes rule: P(state|all_obs) ∝ ∏P(obs_i|state)P(state). MAP estimate is fused result.""",
            "C": """Use covariance intersection for decentralized fusion. When sensor correlations unknown, combine as: P^{-1} = ω_1 P_1^{-1} + ω_2 P_2^{-1} where ω_i optimized. Handles unknown correlations conservatively.""",
            "D": """Apply sheaf theory from algebraic topology. Sensors = sections over regions. Sheaf consistency requires overlap agreement. Sheaf Laplacian encodes constraints. Minimize squared cohomology (H^1 ≠ 0 = inconsistency). Localizes faults via cohomology basis.""",
        },
        "answer_key": "D",
    },

        {
        "id": "xdv4_016_graphics_variational_surface",
        "domain_a": "Computer Graphics (Surface Modeling)",
        "domain_b": "Variational Calculus (Optimization)",
        "problem": """You have a noisy 3D scan of a surface (millions of points). You want to reconstruct a smooth surface that fits the data but isn't overly jagged (overfitting). Simply interpolating all points produces unrealistic spikes. How do you balance fitting the data with smoothness?""",
        "hint": "Consider optimization frameworks for functionals over infinite-dimensional spaces.",
        "choices": {
            "A": """Apply variational surface reconstruction. Define energy E = ∫(data_term + λ·smoothness_term). Data penalizes distance to points; smoothness penalizes curvature (∫(∇²f)²). Minimize via Euler-Lagrange → PDE (thin-plate, mean curvature flow). Discretize to sparse linear system.""",
            "B": """Use Poisson surface reconstruction. Compute oriented point cloud normals as gradient field. Solve Poisson equation ∇²χ = ∇·N where χ is indicator function. Extract isosurface at 0.5.""",
            "C": """Apply moving least squares (MLS). For each query point, fit local polynomial weighted by distance to nearby points. Smooth implicit surface = {x : MLS(x) = 0}. Adaptive smoothness via kernel bandwidth.""",
            "D": """Use ball-pivoting algorithm (BPA). Roll virtual ball over point cloud. When ball touches 3 points without interior points, create triangle. Purely geometric, no optimization required.""",
        },
        "answer_key": "A",
    },

        {
        "id": "xdv4_017_db_homology_query_containment",
        "domain_a": "Database Systems (Query Optimization)",
        "domain_b": "Homological Algebra (Pure Mathematics)",
        "problem": """A query optimizer needs to determine if query Q1's results are always contained in query Q2's results (query containment). If so, Q1 ⊆ Q2 can enable rewriting and caching optimizations. For conjunctive queries with joins and projections, testing containment is NP-complete. How do you efficiently check containment or find approximate answers?""",
        "hint": "Consider algebraic structures for reasoning about inclusion relationships.",
        "choices": {
            "A": """Use query fingerprinting with MinHash. Hash query predicates to signatures. Q1 ⊆ Q2 if Jaccard(Q1, Q2) ≈ 1. Approximate containment in sublinear space.""",
            "B": """Apply homomorphism from homological algebra. Q1 ⊆ Q2 iff ∃ homomorphism h: Q2 → Q1 on query graphs (nodes=relations, edges=joins). For acyclic queries, use tree decomposition + DP. Chase procedure computes fixpoint.""",
            "C": """Normalize queries to canonical form via algebraic rewriting rules. Apply commutativity, associativity, distributivity. If canonical forms match, queries equivalent. Extend to containment by checking subgraph isomorphism.""",
            "D": """Use constraint-based analysis. Encode query semantics as SMT formulas. Q1 ⊆ Q2 if Q2 ∧ ¬Q1 is unsatisfiable. Use Z3 or similar SMT solver for automated checking.""",
        },
        "answer_key": "B",
    },

        {
        "id": "xdv4_018_net_stochastic_tcp_fairness",
        "domain_a": "Networking (TCP Fairness)",
        "domain_b": "Stochastic Processes (Probability)",
        "problem": """Multiple TCP flows share a bottleneck link. Each flow uses AIMD congestion control. You want to analyze whether flows converge to fair bandwidth shares and how long convergence takes under random packet losses. Deterministic models fail to capture loss randomness. How do you model and analyze fairness under stochastic losses?""",
        "hint": "Consider mathematical frameworks for systems evolving randomly over time.",
        "choices": {
            "A": """Use fluid-flow approximation. Model TCP as continuous ODE: dx/dt = a - bx²p (additive increase, multiplicative decrease). Analyze equilibrium by setting dx/dt = 0. Deterministic analysis suffices for average behavior.""",
            "B": """Apply game theory. Model TCP flows as players competing for bandwidth. Analyze Nash equilibrium where no flow can improve by unilateral deviation. Social optimality via price of anarchy.""",
            "C": """Apply stochastic approximation theory and Markov processes. Model AIMD as SDE: dx = a dt - b dN (Poisson loss). Lyapunov stability with potential function (proportional fairness). Almost-sure convergence to equilibrium. Diffusion approximation for large timescales.""",
            "D": """Use discrete-event simulation with ns-3 or similar. Model packet-level dynamics, random losses, timeouts. Run Monte Carlo simulations, measure fairness convergence empirically.""",
        },
        "answer_key": "C",
    },

        {
        "id": "xdv4_019_verify_game_semantics_synthesis",
        "domain_a": "Program Verification (Synthesis)",
        "domain_b": "Game Semantics (Logic)",
        "problem": """You want to automatically synthesize a program that satisfies a formal specification (e.g., 'sort an array correctly'). Exhaustive search over programs is intractable. You need a structured approach that explores the space of programs guided by the specification. How do you frame synthesis as a search problem with convergence guarantees?""",
        "hint": "Consider adversarial frameworks where two players compete.",
        "choices": {
            "A": """Use genetic programming. Generate random program population, evaluate against spec (fitness), breed high-fitness programs (crossover, mutation). Evolve toward satisfying program.""",
            "B": """Apply constraint-based synthesis (CEGIS - CounterExample-Guided Inductive Synthesis). Synthesizer proposes program, verifier finds counterexample. Iterate until no counterexamples. Used in Sketch, Rosette.""",
            "C": """Use type-directed synthesis. Given desired output type, search space of well-typed programs. Type system prunes search space. Enumerate small programs, check against examples. Scales to simple specifications.""",
            "D": """Apply game semantics and synthesis games. Frame as two-player: Synthesizer proposes program fragments, Environment provides inputs/counterexamples. Winning strategy = satisfying program. Use LTL/CTL specs → parity game/Büchi automaton. Solve via attractor computation.""",
        },
        "answer_key": "D",
    },

        {
        "id": "xdv4_020_quantum_noncomm_surface_code",
        "domain_a": "Quantum Computing (Error Correction)",
        "domain_b": "Non-Commutative Geometry (Topology)",
        "problem": """Quantum states are fragile: decoherence and gate errors rapidly corrupt qubits. Simple repetition codes don't work (no-cloning theorem). You need to encode logical qubits in a way that allows error detection and correction without measuring (collapsing) the quantum state. How do you design a quantum error correcting code that works with non-commuting observables?""",
        "hint": "Consider topological structures on non-commutative spaces.",
        "choices": {
            "A": """Apply surface codes (toric code) from topological QEC. Encode logical qubit in 2D lattice Hamiltonian ground state (stabilizer code). Measure stabilizers (Pauli products) on plaquettes. Errors = anyonic excitations. Syndrome doesn't collapse logical state. Correct via min-weight matching. Threshold theorem.""",
            "B": """Use Shor code: encode 1 logical qubit in 9 physical qubits. Correct bit-flips (X errors) and phase-flips (Z errors) independently via separate encoding layers. First quantum error-correcting code discovered.""",
            "C": """Apply quantum teleportation with error detection. Entangle multiple Bell pairs. Teleport qubit through pairs, checking parity. If errors detected, retry with fresh pairs. Probabilistic but eventually succeeds.""",
            "D": """Use decoherence-free subspaces (DFS). Encode in symmetric subspace of multiple qubits that is invariant under collective noise. System-environment symmetry protects encoded states from certain error types.""",
        },
        "answer_key": "A",
    },

]


@register("xdomain_v4")
def load_xdomain_v4(n: int = 20, seed: int = 42) -> list[Question]:
    """Load XDomain V4 - harder cross-domain synthesis benchmark (MULTIPLE CHOICE).

    20 problems targeting baseline accuracy 20-40%.
    Redesigned as MC in iter266 to fix grading bug (iter265 had rubric-grading that runners didn't support).
    """
    if n > len(XDOMAIN_V4_PROBLEMS):
        raise ValueError(f"Only {len(XDOMAIN_V4_PROBLEMS)} problems available, requested {n}")

    rng = random.Random(seed)
    selected = rng.sample(XDOMAIN_V4_PROBLEMS, n)

    questions = []
    for prob in selected:
        question_text = f"""**Domain: {prob['domain_a']}**

{prob['problem']}

**Hint**: {prob['hint']}

Identify the relevant concept/technique from {prob['domain_b']} and explain how to apply it."""

        # Format choices as A/B/C/D list
        choices_list = [prob['choices']['A'], prob['choices']['B'], prob['choices']['C'], prob['choices']['D']]

        questions.append(Question(
            id=prob["id"],
            dataset="xdomain_v4",
            subject=f"{prob['domain_a']} + {prob['domain_b']}",
            question=question_text,
            choices=choices_list,  # Now MC format!
            answer_key=prob["answer_key"],  # A/B/C/D
            metadata={
                "domain_a": prob["domain_a"],
                "domain_b": prob["domain_b"],
                # Removed gold_answer, rubric, max_score (no longer applicable for MC)
            }
        ))

    return questions


# XDomain V5: Biology ↔ Cryptography (First 10 problems - COMPLETE MC)
# Designed in iter277, converted to MC in iter278-279
# Baseline difficulty target: 15-35%, AS advantage expected +50-60pp

XDOMAIN_V5_PROBLEMS = [
    {
        "id": "xdv5_001_dna_otp_storage",
        "domain_a": "Cryptography (Key Management)",
        "domain_b": "Molecular Biology (DNA Storage)",
        "problem": """One-time pads provide perfect secrecy but require storing massive amounts of random key material (equal to all messages ever sent). A nation-state needs to store 1 petabyte of OTP keys securely for decades. Magnetic tape degrades, HDDs fail, and electronic storage is vulnerable to EMP. How can you achieve ultra-high-density, long-term, tamper-evident key storage?""",
        "hint": "Consider information storage mechanisms used by living organisms for billions of years.",
        "choices": {
            "A": """Apply DNA-based storage from molecular biology. DNA stores ~215 petabytes per gram (10^18 bytes/gram), far exceeding electronic density. Synthesize random sequences, store in sealed vials. Half-life: 500+ years in cold/dark conditions (vs decades for tape). Tamper-evidence: sequence verification via PCR amplification. Read keys via sequencing (Illumina/ONT), error correction via Reed-Solomon codes (biological systems use redundancy). Physical security via biometric access to vaults. Combines cryptographic storage with biological ultra-density.""",
            "B": """Use quantum key distribution (QKD) to generate OTP keys on-demand via entangled photon pairs. Store only initial quantum state (small), generate keys when needed. Avoids storage problem entirely but requires quantum channel infrastructure.""",
            "C": """Apply Reed-Solomon erasure coding to existing magnetic tape. Split 1 PB into 10 petabyte fragments, store with 5× redundancy across geographically distributed sites. Density unchanged but fault tolerance via geographical diversity.""",
            "D": """Use hierarchical key derivation (HKDF) from small master secret. Generate OTP keys deterministically from hash chain. Reduces storage to master key (~256 bits) but breaks perfect secrecy (pseudorandom, not truly random).""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_002_immune_negative_selection",
        "domain_a": "Cybersecurity (Intrusion Detection)",
        "domain_b": "Immunology (Adaptive Immunity)",
        "problem": """Signature-based malware detection fails against zero-day exploits (never-seen-before attacks). Machine learning anomaly detectors produce high false positives because they don't understand 'normal'. You need a system that distinguishes 'self' (legitimate) from 'non-self' (attack) without prior examples of attacks. How do you build a detector that learns only from normal data?""",
        "hint": "Consider how the immune system distinguishes self from pathogen without seeing every possible pathogen.",
        "choices": {
            "A": """Apply negative selection algorithm from adaptive immunity (T-cell development). Biological analogy: immature T-cells that react to self-antigens are eliminated in thymus (negative selection), remaining cells react only to non-self. Algorithm: generate random detectors, eliminate any that match normal traffic (self), retain remainder as anomaly detectors. Detection: if new traffic matches any detector → alarm. Learns representation of normality without seeing attacks. Clonal selection (detector proliferation) for adaptive response.""",
            "B": """Use supervised anomaly detection with synthetic attack data. Generate fake attacks via GANs, train classifier to distinguish normal from synthetic attacks. Requires labeled attack data (even if synthetic).""",
            "C": """Apply Bayesian networks to model normal behavior. Learn probability distribution P(normal), flag low-probability events as anomalies. High false positive rate on rare-but-legitimate events.""",
            "D": """Use simple threshold-based detection. Calculate statistical mean/stddev of normal traffic, flag anything >3 standard deviations. Detects outliers but not sophisticated attacks that mimic normal distributions.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_003_quorum_sensing_consensus",
        "domain_a": "Distributed Systems (Byzantine Consensus)",
        "domain_b": "Microbiology (Bacterial Communication)",
        "problem": """A decentralized network of 100 nodes needs to agree on a value, but up to 33 nodes may be Byzantine (arbitrary malicious behavior, fake messages). Traditional consensus (Paxos, Raft) assumes honest majority and fails under Byzantine faults. You need consensus that tolerates up to f malicious nodes without a trusted leader. How do you achieve agreement in a fully adversarial environment?""",
        "hint": "Consider how bacterial colonies coordinate collective behavior without central control.",
        "choices": {
            "A": """Apply quorum sensing from microbiology to Byzantine fault-tolerant consensus. Bacteria secrete signaling molecules (autoinducers), collective behavior activates when concentration exceeds threshold (quorum). Algorithm: nodes broadcast signed proposals, collect 2f+1 signatures (quorum) before committing (PBFT-style). Threshold prevents f Byzantine nodes from forcing decision. Biological parallel: sybil resistance via chemical diffusion limits, not identities. Combines threshold cryptography (signatures) with quorum-based coordination.""",
            "B": """Use proof-of-work consensus (Bitcoin-style). Each node mines blocks, longest chain wins. Tolerates Byzantine nodes via computational hardness but requires massive energy and has high latency.""",
            "C": """Apply Paxos with leader election. Designate leader via timeout-based election, leader proposes values, majority accepts. Fails if leader is Byzantine (can propose conflicting values).""",
            "D": """Use simple majority voting. Each node votes, take majority decision. Works for crash faults but Byzantine nodes can vote inconsistently or split the vote, breaking consensus.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_004_protein_misfolding_collision",
        "domain_a": "Cryptanalysis (Hash Function Attacks)",
        "domain_b": "Biochemistry (Protein Folding)",
        "problem": """A cryptographic hash function must be collision-resistant: computationally infeasible to find two inputs that produce the same output. But MD5 and SHA-1 have fallen to chosen-prefix collision attacks, where attackers find colliding messages with controlled prefixes. You need to understand how structured inputs can force collisions. What physical analogy explains why certain structured inputs lead to collisions?""",
        "hint": "Consider how certain molecules force proteins to adopt the wrong shape, causing disease.",
        "choices": {
            "A": """Apply prion misfolding dynamics from protein biochemistry. Normal proteins (PrP^C) fold into stable conformations (native hash output). Prions (PrP^Sc) are misfolded versions that catalyze misfolding of normal proteins into identical misfolded shape (collision: different input → same output). Chosen-prefix attack analogy: carefully designed molecular chaperone (attacker-chosen prefix) guides folding pathway to specific misfolded state. Thermodynamic inevitability: some fold pathways lead to same low-energy misfolded state regardless of starting sequence.""",
            "B": """Birthday paradox explains collisions. In a hash space of 2^128, after √(2^128) = 2^64 hashes, collision probability approaches 1. This is why MD5 (128-bit) fell before SHA-256 (256-bit). Pure probability, no biological analogy.""",
            "C": """Quantum tunneling through energy barriers. Hash function has local minima (outputs), quantum tunneling allows inputs to 'jump' to same minimum. Requires quantum computer for collision search.""",
            "D": """Avalanche effect failure. Good hash functions have avalanche property (one bit change → 50% output bits flip). Weak hash functions have correlation between inputs and outputs, making collisions easier to find via differential cryptanalysis.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_005_camouflage_steganography",
        "domain_a": "Information Hiding (Steganography)",
        "domain_b": "Zoology (Adaptive Camouflage)",
        "problem": """Traditional steganography (LSB embedding in images) is detectable via statistical analysis (steganalysis detects non-natural distributions). You need to hide a message in an image such that statistical detectors cannot distinguish it from natural images. The hidden data must adapt to image content. How do you make steganography undetectable by matching natural image statistics?""",
        "hint": "Consider how cuttlefish and octopuses change skin patterns to match their environment in real-time.",
        "choices": {
            "A": """Apply adaptive camouflage mechanisms from cephalopod biology. Cuttlefish sense substrate texture/color via vision, modulate chromatophore patterns to match distribution. Steganography analogy: analyze cover image's statistical 'texture' (DCT coefficients, noise patterns), embed message bits by modulating coefficients to preserve statistical distribution (model-based embedding). Use GAN-generated perturbations trained on natural images (neural camouflage). Key insight: embedding adapts to local image statistics (like chromatophores adapt to substrate).""",
            "B": """Use provably undetectable steganography via model-based embedding. Assume detector has learned model of natural images P(image). Embed message by rejecting samples from P until embedded bits match message. Computationally expensive (rejection sampling) but provably matches P.""",
            "C": """Apply cryptographic steganography (steganographic file systems). Encrypt message, store in filesystem with deniable encryption (multiple keys reveal different data). Hides message existence but doesn't use image statistics.""",
            "D": """Use LSB matching (±1 embedding) instead of LSB replacement. Randomly add or subtract 1 from pixels to embed bits. Reduces statistical artifacts compared to LSB replacement but still detectable via histogram analysis.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_006_red_queen_adversarial_ml",
        "domain_a": "Machine Learning Security (Adversarial Robustness)",
        "domain_b": "Evolutionary Biology (Coevolution)",
        "problem": """You deploy a spam filter (ML classifier). Spammers adapt their emails to evade the filter (adversarial examples). You retrain on adversarial examples. Spammers adapt again. This arms race continues indefinitely, with no equilibrium. Static defense fails. How do you model and defend against this dynamic adversarial coevolution?""",
        "hint": "Consider evolutionary dynamics between parasites and hosts over geological time.",
        "choices": {
            "A": """Apply Red Queen hypothesis from evolutionary biology: 'Running to stay in place' (host-parasite coevolution). Parasites evolve to exploit hosts → hosts evolve resistance → parasites evolve counter-resistance (perpetual arms race, no stable equilibrium). ML analogy: attacker (parasite) evolves adversarial examples → defender (host) evolves robust classifier → attacker evolves new attacks. Defense strategy: continual learning (evolutionary adaptation), diversity (ensemble = genetic diversity reduces single-point-of-failure), and arms-race modeling (coevolutionary dynamics).""",
            "B": """Use adversarial training with augmented data. Generate adversarial examples via FGSM/PGD, retrain classifier on adversarial set. Improves robustness but static (doesn't adapt to new attacks after deployment).""",
            "C": """Apply game-theoretic min-max optimization. Model attacker and defender as players, find Nash equilibrium strategy. Assumes both have full knowledge and reach equilibrium (not perpetual arms race).""",
            "D": """Use certified robustness via randomized smoothing. Add Gaussian noise during inference, prove L2-norm robustness bounds. Provides guarantees but only for specific threat models, doesn't address dynamic adaptation.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_007_neural_timing_covert_channel",
        "domain_a": "Information Security (Covert Channels)",
        "domain_b": "Neuroscience (Neural Coding)",
        "problem": """Two processes on a secure system (sandboxed, no shared memory, encrypted network) need to communicate covertly without detection. Traditional covert channels (CPU cache timing, network timing) are detectable via statistical analysis. You need a channel that uses naturally variable timing to hide messages. How do you encode information in timing patterns that appear random?""",
        "hint": "Consider how neurons encode information not just in spike presence but in precise timing.",
        "choices": {
            "A": """Apply neural temporal coding (spike-timing) from neuroscience. Neurons encode information via precise timing of action potentials (millisecond precision), not just firing rate. Covert channel analogy: sender modulates timing of legitimate operations (network packets, disk I/O) to encode bits. Use inter-spike interval (ISI) encoding: short interval = 0, long = 1 (or phase coding). Receiver detects timing patterns. Key: timing jitter matches natural variability (synaptic noise, refractory period) making it indistinguishable from normal timing variance.""",
            "B": """Use CPU cache side-channel (Flush+Reload). Attacker flushes cache line, victim accesses it (or doesn't), attacker measures reload time. Timing distinguishes access patterns. Well-known technique but detectable via cache monitoring.""",
            "C": """Apply spread-spectrum communication. Encode bits across wide frequency band, appear as noise. Requires bandwidth and doesn't exploit natural timing variability (uses explicit modulation).""",
            "D": """Use TCP timestamp covert channel. Embed bits in TCP timestamp option field. Visible in packet headers, easy to filter or detect via deep packet inspection.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_008_hgt_capability_sharing",
        "domain_a": "Malware Analysis (Capability Propagation)",
        "domain_b": "Genetics (Horizontal Gene Transfer)",
        "problem": """Multiple malware families in the wild suddenly acquire the same exploit capability (e.g., EternalBlue SMB exploit) within weeks. They are not variants of the same family (different codebases). Traditional malware evolution (vertical: parent to child via replication) doesn't explain this. How does capability spread laterally across unrelated malware families?""",
        "hint": "Consider how antibiotic resistance spreads rapidly across unrelated bacterial species.",
        "choices": {
            "A": """Apply horizontal gene transfer (HGT) from bacterial genetics. Bacteria acquire genes from non-parental sources via transformation (uptake of DNA), transduction (viral transfer), conjugation (plasmid exchange). Rapid spread of resistance genes across species. Malware analogy: exploit modules are 'genes', shared via underground forums (transformation), exploit kits (transduction), or modular malware frameworks (conjugation/plasmids). Unrelated families incorporate same exploit module (lateral transfer), explaining rapid capability propagation across phylogenetically distant malware clades.""",
            "B": """Convergent evolution explains similar capabilities. Separate malware families independently develop same exploit due to common vulnerability (EternalBlue targets SMB flaw). Parallel evolution, not transfer.""",
            "C": """Supply chain compromise. All families use same third-party library or toolchain, which is compromised to include exploit. Common ancestor, not lateral transfer.""",
            "D": """Code obfuscation makes unrelated malware appear similar. Polymorphic engines transform code while preserving semantics, creating false appearance of shared capability when actually independent implementations.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_009_circadian_access_control",
        "domain_a": "Access Control (Authentication)",
        "domain_b": "Chronobiology (Circadian Rhythms)",
        "problem": """A secure facility needs to enforce time-based access control: employees can enter only during their scheduled shifts, even if their credentials are valid. But timezone handling is complex, daylight saving causes bugs, and synchronized clocks are vulnerable to manipulation. You need a biologically-inspired, decentralized time-keeping mechanism that's robust to clock skew and manipulation. How do you build a distributed time-based access control system?""",
        "hint": "Consider how organisms maintain internal time sense without external clocks.",
        "choices": {
            "A": """Apply circadian rhythm mechanisms from chronobiology. Biological clocks use transcription-translation feedback loops (TTFL): genes produce proteins that inhibit their own transcription (~24h cycle), synchronized via external cues (Zeitgebers: light). Access control analogy: distributed nodes maintain local 'genetic' oscillators (cryptographic accumulators incremented periodically), synchronized via external events (shift changes, network heartbeats). Phase coherence = valid access window. Robust to clock manipulation (endogenous oscillator) and drift (Zeitgeber resynchronization).""",
            "B": """Use GPS time synchronization. All nodes sync to GPS atomic clocks (UTC), enforce access windows via synchronized timestamps. Accurate but vulnerable to GPS spoofing and requires external infrastructure.""",
            "C": """Apply blockchain timestamps. Nodes submit access requests to blockchain, miners timestamp via proof-of-work. Decentralized but high latency (~10 min for Bitcoin) and energy cost.""",
            "D": """Use NTP (Network Time Protocol) with Byzantine fault tolerance. Nodes query multiple NTP servers, filter outliers, agree on median time. Standard approach but vulnerable to majority compromise of NTP servers.""",
        },
        "answer_key": "A",
    },

    {
        "id": "xdv5_010_swarm_ddos_detection",
        "domain_a": "Network Security (DDoS Detection)",
        "domain_b": "Entomology (Swarm Intelligence)",
        "problem": """Distributed Denial-of-Service (DDoS) attacks use thousands of bots to flood a server. Traditional detection (threshold-based) has high false positives (flash crowds look like DDoS). Signature-based detection fails against novel attack patterns. You need to detect coordinated behavior in massive traffic without knowing attack signatures in advance. How do you identify coordinated swarm behavior in network traffic?""",
        "hint": "Consider how ant colonies collectively optimize foraging without central coordination.",
        "choices": {
            "A": """Apply ant colony optimization (ACO) and swarm intelligence from entomology. Ants deposit pheromone trails → reinforcement of successful paths → collective optimization emerges. DDoS detection analogy: network flows are 'ants', packet headers/timing are 'pheromones'. Detect anomalous convergence: many flows from different sources suddenly converge on same target with similar patterns (coordinated swarm). ACO-based detector: model normal traffic as diverse foraging (many trails), attack as coordinated convergence (single strong trail). Stigmergy (indirect coordination) reveals DDoS coordination.""",
            "B": """Use deep learning (LSTM) on traffic flows. Train recurrent network to predict next packet, flag anomalies as deviations from prediction. Effective but requires labeled training data and doesn't explicitly model coordination.""",
            "C": """Apply information theory (entropy analysis). Calculate Shannon entropy of source IPs, ports, packet sizes. DDoS = low entropy (uniform). Simple metric but doesn't detect sophisticated attacks that randomize headers.""",
            "D": """Use rate limiting with CAPTCHA challenges. Throttle high-volume sources, present CAPTCHA to verify human. Effective for some bots but breaks API access and doesn't detect low-rate DDoS.""",
        },
        "answer_key": "A",
    },
]


@register("xdomain_v5")
def load_xdomain_v5(n: int = 10, seed: int = 42) -> list[Question]:
    """Load XDomain V5 - Biology ↔ Cryptography synthesis (MULTIPLE CHOICE).

    10 problems targeting baseline accuracy 15-35%, AS advantage expected +50-60pp.
    Designed in iter277, converted to MC in iter278-279.
    """
    if n > len(XDOMAIN_V5_PROBLEMS):
        raise ValueError(f"Only {len(XDOMAIN_V5_PROBLEMS)} problems available, requested {n}")

    rng = random.Random(seed)
    selected = rng.sample(XDOMAIN_V5_PROBLEMS, n)

    questions = []
    for prob in selected:
        question_text = f"""**Domain: {prob['domain_a']}**

{prob['problem']}

**Hint**: {prob['hint']}

Identify the relevant concept/technique from {prob['domain_b']} and explain how to apply it."""

        # Format choices as A/B/C/D list
        choices_list = [prob['choices']['A'], prob['choices']['B'], prob['choices']['C'], prob['choices']['D']]

        questions.append(Question(
            id=prob["id"],
            dataset="xdomain_v5",
            subject=f"{prob['domain_a']} + {prob['domain_b']}",
            question=question_text,
            choices=choices_list,
            answer_key=prob["answer_key"],  # A/B/C/D
            metadata={
                "domain_a": prob["domain_a"],
                "domain_b": prob["domain_b"],
            }
        ))

    return questions
