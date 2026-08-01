import unittest


class Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode; self.stdout = stdout


class Process:
    def __init__(self, result=None): self.calls = []; self.result = result or Result()
    def run(self, argv, **kwargs): self.calls.append((argv, kwargs)); return self.result


class Policy:
    machine_id = "sb-0123456789ab"
    digest = "a" * 64


class TestManagedMachine(unittest.TestCase):
    def test_all_machine_lifecycle_uses_fixed_helper_and_digest(self):
        from sandbox.runtimes.managed.machine import ManagedMachine
        process = Process(); machine = ManagedMachine(process=process, helper="/fixed/helper")
        plan = machine.plan(Policy())
        machine.start_minimal(plan); machine.status(plan); machine.stop(plan)
        self.assertEqual([call[0][3] for call in process.calls],
                         ["machine-start-minimal", "machine-status", "machine-stop"])
        for argv, kwargs in process.calls:
            self.assertEqual(argv[-2:], (Policy.machine_id, Policy.digest))
            self.assertEqual(kwargs["timeout"], 120)

    def test_start_failure_never_reports_a_running_machine(self):
        from sandbox.runtimes.managed.machine import ManagedMachine
        machine = ManagedMachine(process=Process(Result(1)), helper="helper")
        with self.assertRaises(RuntimeError): machine.start_minimal(machine.plan(Policy()))


if __name__ == "__main__": unittest.main()
