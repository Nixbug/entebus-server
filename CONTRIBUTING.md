## 🤝 Contributing

We welcome contributions from the community! 🚀  
Whether it’s fixing a bug, improving documentation, or suggesting new features — all contributions are valuable.  

### Ways to Contribute
- 🐛 **Report bugs** by opening an issue.
- 💡 **Suggest features** to improve the project  
- 📝 **Improve documentation** (README, CONTRIBUTING, tutorials)  
- 🔧 **Submit code** via Pull Requests (PRs)  

### Branching Rules

All branches **must** follow the naming convention:
- Include a **short but descriptive** name of the work being done.  
- Use **kebab-case** (`-` between words).  
- Keep it concise (avoid very long branch names).  

#### Prefixes
- **F** → Feature (must be linked to a GitHub Issue)  
- **B** → Bug fix (must be linked to a GitHub Issue)  

#### Rules for Features (F)
- A **feature branch must always be created from a GitHub Issue**.  
- The Issue should contain:
  - A **clear description** of the task  
  - Any **relevant technical details or references**  
  - Acceptance criteria or expected outcome  
- Use the issue number as the branch number.

#### Rules for Bugs (B)
- A **bug branch must be linked to a GitHub Issue** describing:
  - The problem and how to reproduce it  
  - Screenshots, logs, or error traces (if applicable)  
- Use the issue number as the branch number.

#### Examples
- `F001/Implement-scheduler-triggering-mechanism` → Feature branch  
- `B001/Fix-authentication-timeout` → Bug fix branch  


#### Workflow
1. **Open an Issue** for your feature or bug if it does not exist already.  
2. **Discuss/assign** the Issue before starting work.  
3. Create your branch from the `develop` branch:  
```bash
   git checkout develop
   git checkout -b F002/Add-route-searching
```
2. Commit and push changes to your branch.
3. Open a Pull Request (PR) into `develop`.
