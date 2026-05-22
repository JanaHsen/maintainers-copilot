"""Tool primitives — composable building blocks the chatbot agent loop calls.

Each module here exposes one tool primitive as a typed-outcome function
(Rule 11): no exceptions escape, every failure mode is a discriminated
variant of the return type. The chatbot service in Part 2 will compose
these primitives; Part 1 ships them with stable signatures so the agent
loop can land cleanly later.
"""
