import unittest
from pathlib import Path
import tempfile
from executor import validate_inputs, select_index, run_label


class InputTests(unittest.TestCase):
    def base(self, **updates):
        env = {'INPUT_REPOSITORY': 'owner/repo', 'INPUT_SHA': 'a' * 40,
               'INPUT_RUNNER': 'avrea-ubuntu-latest-4-vcpu', 'INPUT_REQUEST_ID': 'request-123',
               'INPUT_COMMAND': 'make test', 'INPUT_MATRIX': '[]', 'INPUT_PARALLEL': '3',
               'INPUT_TIMEOUT': '20', 'INPUT_FETCH': 'test-results/**'}
        return {**env, **updates}

    def test_single_command(self):
        matrix, parallel, timeout = validate_inputs(self.base())
        self.assertEqual(matrix, [{'index': 0, 'command': 'make test'}])
        self.assertEqual((parallel, timeout), (3, 20))

    def test_parallel_matrix(self):
        matrix, _, _ = validate_inputs(self.base(INPUT_MATRIX='["make lint","make test","make build"]'))
        self.assertEqual([item['index'] for item in matrix], [0, 1, 2])

    def test_moving_ref_refused(self):
        with self.assertRaises(ValueError):
            validate_inputs(self.base(INPUT_SHA='main'))

    def test_artifact_escape_refused(self):
        for glob in ('../private', '/etc/passwd', '.git/**', 'C:\\private', '!test-results'):
            with self.subTest(glob=glob), self.assertRaises(ValueError):
                validate_inputs(self.base(INPUT_FETCH=glob))

    def test_unsupported_arm_refused(self):
        for label in ('avrea-ubuntu-latest-arm-32-vcpu', 'avrea-ubuntu-22.04-arm-4-vcpu'):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_inputs(self.base(INPUT_RUNNER=label))

    def test_invalid_matrix_refused(self):
        for matrix in ('{}', '[1]', '[""]'):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                validate_inputs(self.base(INPUT_MATRIX=matrix))

    def test_explicit_github_timing_control(self):
        matrix, _, _ = validate_inputs(self.base(INPUT_RUNNER='ubuntu-latest'))
        self.assertEqual(matrix[0]['command'], 'make test')

    def test_invalid_index_refused(self):
        with self.assertRaises(ValueError):
            validate_inputs(self.base(INPUT_PACKAGE_INDEX='other'))

    def test_index_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(select_index(root, ''), 'avrea')
            for name in ('uv.lock', 'poetry.lock'):
                path = root / name
                path.write_text('https://pypi.org/simple')
                self.assertEqual(select_index(root, ''), 'official')
                self.assertEqual(select_index(root, 'avrea'), 'avrea')
                path.unlink()
            (root / 'requirements.txt').write_text('package==1 --hash=sha256:abc')
            self.assertEqual(select_index(root, ''), 'official')
            self.assertEqual(select_index(root, 'official'), 'official')


if __name__ == '__main__':
    unittest.main()


class RunLabelTests(unittest.TestCase):
    def test_avrea_linux_runs_free_on_github(self):
        for label in ('avrea-ubuntu-latest-2-vcpu', 'avrea-ubuntu-24.04-8-vcpu', 'avrea-ubuntu-latest-4-vcpu-8gb'):
            with self.subTest(label=label):
                self.assertEqual(run_label(label), 'ubuntu-latest')

    def test_boat_and_others_pass_through(self):
        for label in ('boat', 'boat-large', 'avrea-macos-26-8-vcpu', 'avrea-ubuntu-latest-arm-4-vcpu', 'ubuntu-latest'):
            with self.subTest(label=label):
                self.assertEqual(run_label(label), label)

    def test_boat_labels_validate(self):
        env = {'INPUT_REPOSITORY': 'owner/repo', 'INPUT_SHA': 'a' * 40, 'INPUT_RUNNER': 'boat-large',
               'INPUT_REQUEST_ID': 'r1', 'INPUT_COMMAND': 'true', 'INPUT_MATRIX': '[]',
               'INPUT_PARALLEL': '1', 'INPUT_TIMEOUT': '5', 'INPUT_FETCH': ''}
        validate_inputs(env)
