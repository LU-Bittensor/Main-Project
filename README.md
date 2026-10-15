# Welcome to Medilinks Main Project

This repository is where we build infrastructure for **individual autonomy over health data**: people should decide who can see their records, for how long, and under what conditions — without handing permanent control to a central intermediary.

We combine on-chain access control, encrypted storage, and a Bittensor subnet so that permissions, incentives, and data handling can stay aligned with the patient (or account holder) rather than a single platform.

## Repository layout

| Folder | What it is |
| --- | --- |
| **`subnet/`** | Miner and validator logic for our Bittensor subnet — the network side of the project. |
| **`markdowns/`** | Project notes and decision records: contract design, storage choices, and other context for contributors. |
| **`key-generation/`** | Smart contract logic and related scripts (access registry, deployment, local tooling). |

Start with the markdowns if you want the “why,” then dig into `key-generation` for contracts/scripts and `subnet` for miner/validator code.
