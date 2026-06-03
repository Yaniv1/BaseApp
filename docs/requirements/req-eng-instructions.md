# Requirements Engineering Instructions

This file provides instructions for requirements engineering:
1. Specify BaseApp requirements in docs/requirements/base.json.
2. Specify Variant App requirements in docs/requirements/app.json.
3. Specify requirements according to the following structure:
    {
        "id": "TEMPLATE-001",
        "title": "Template Requirement",
        "description": "Impreative statement about what the system must do, without specifying how it should be done.",
        "priority": "Low|Medium|High|Critical",
        "comments": [
            {
                "author": "John Doe",
                "date": "2024-06-01",
                "comment": "This is a sample comment on the requirement."
            }
        ],
        "breakdown": [
            {
                <sub requirement 1>
            },
            {
                <sub requirement 2>
            },
            ...
        ],
        "solution": [
            {
                "feature": "<assigned feature 1 id>",
                "role": "description of the feature's role in implementing the requirement",
                "implementation": 0.0-1.0, <completion rate between 0 and 1>
                "verification": 0.0-1.0 <completion rate between 0 and 1>
                "deployment": 0.0-1.0   <completion rate between 0 and 1>
            },
            {
                "feature": "<assigned feature 2 id>",
                "role": "description of the feature's role in implementing the requirement",
                "implementation": 0.0-1.0, <completion rate between 0 and 1>
                "verification": 0.0-1.0 <completion rate between 0 and 1>
                "deployment": 0.0-1.0   <completion rate between 0 and 1>
            }
        ],
        
    }

4. Breakdown:
    - break down the requirement into more detailed sub-requirements, verification criteria, etc.
    - keep breaking down until sub-requirements become complete and implementable.
    - for each requirement try to define clearly:
        - input requirement(s)
        - process requirement(s)
        - output requirement(s)
        - structure requirement(s) - minimize constraints on structure.
        - security requirement(s)
        - safety requirement(s)
        - performance requirement(s)
5. Design:
    - If the requirement is concrete enough and not abstract
    - Decide how the requirement and its sub-requirements are going to be implemented using the architecture
    - Minimizing the architecture impact and avoiding the unnecessary creation of new items beyond what is necessary to complete the implementation, unless the requirement explicitly specifies what to build or how to build it.
    - Specify the features that are assigned to implement the requirement. Ideally each requirement should be implemented by a single feature. If multiple features are required to implement the requirement, it means that the requirement might need to be broken down into more basic requirements.
6. Implement:
    - Adhere to the approved design and implement it in the features
    - Update the implementation status after each feature.
7. Verify:
    - Create tests that prove the requirement.
    - Run the tests to ensure that the solution works.
8. Deploy:
    - Finalize the modification to the system, update the documentation, versions, and update history, stage, commit, and push.