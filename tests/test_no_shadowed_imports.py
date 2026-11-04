import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src' / 'vagas'


def imported_names(nodes):
    names = set()
    for n in nodes:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {a.asname or a.name.split('.')[0] for a in n.names}
    return names


class ShadowedImportTests(unittest.TestCase):
    def test_function_never_reimports_a_module_level_name(self):
        """Importar de novo dentro da função torna o nome local à função INTEIRA: um uso antes do import vira
        UnboundLocalError (derrubou o `vagas run` em 30/10 com o `notify` em process_queue)."""
        problems = []
        for path in SRC.rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            top = imported_names(tree.body)
            for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                for name in imported_names(ast.walk(fn)) & top:
                    problems.append(f'{path.name}:{fn.lineno} {fn.name} reimporta {name}')
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
