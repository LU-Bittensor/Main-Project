# Project Overview
This repository contains basic code for taking user input and appending it to a file. The user should also be able to review the file contents within the program. The executable is named **[executable_name]**

## Tech Stack and Architecture
* **Language** C++

---

# Development Guidelines

## Setup and Run Commands
* **Execute Tests:** `test_names`
    - Ensure that the program runs and that the text in `output_file` matches exactly what you input to test
        - The newline written after each input should be included in the exact match

## Coding Standards
* **Comments:** Document all functions with standard documentation
    - Document any third-party dependency and how it is used; standard-library functions do not require dependency comments.
* **Version Formatting:** Use a v0.0.0 format
    - The initial version for the project should be v1.0.0
    - The first number should be incremented upon major changes to the codebase
    - The second number should be incremented upon minor changes
    - The third number should be incremented upon the completion of bug fixes
    - In the README, include the time of version completion in UTC+0 along with the version number
        - Timestamp Format: YYYY-MM-DD HH:MM:SS UTC
    - Documentation changes would indicate a bug fix incrementation
    - Build/configuration changes would indicate a minor version change
* **Version Control:** Use git for version control
    - Commits should be made automatically after each completed, verified functional change, or after 100 lines of code added/modified
    - For this project, commits will be made on [choose branch and permissions/responsibilities] 
    - Commits should not be pushed to the remote repository until all aspects of verification have been passed
    - [Explicit/No] approval is required for pushing to the remote repository

---

# Verification Checklist
Before finalizing any code modification or presenting a solution, verify that:
1. The program runs and passes all [unit/custom/]
2. No security credentials or `.env` variables are hardcoded into the codebase.
3. The code is reasonably condensed without sacrificing readability and while maintaining proper documentation

# After Verification
After all verification tests pass, if no README is present, create one. Update the README with the new program version number and the time of its completion. Update the README with any new dependencies necessary for the execution of the project. At the end, push all necessary files (excluding any .out and .txt files) upstream to the remote repository.
