import unittest
from executor import validate_inputs


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


if __name__ == '__main__':
    unittest.main()
