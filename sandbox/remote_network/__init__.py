"""Sandbox development network ranges on a remote (spec 063 US1).

A remote without Docker ``default-address-pools`` still runs development
networks when an operator assigns a development range: every network Sandbox
creates gets an explicit subnet carved from it (:mod:`.ranges` for the math
and overlap rules), so no daemon restart is needed.
"""
