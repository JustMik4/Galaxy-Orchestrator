"""Interactive project setup. Imports are copies into a new destination."""
import sys

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 ou superior e necessario. Instale e abra Iniciar.bat novamente.')

import json
import os
from pathlib import Path
import shutil
import uuid

from installer import install, safe_path, validate_project
from multicontroller import normalized_path

SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__'}


def excluded(name):
    lower = name.casefold()
    return lower in SKIP_DIRS or lower == '.env' or (lower.startswith('.env.') and lower not in {'.env.example', '.env.sample', '.env.template'})


def create_project(master, projects, name, source=None, mode='SOLO', preset='balanced'):
    master, projects = safe_path(master), safe_path(projects)
    normalized_path(name)
    if '/' in name or name != name.strip(): raise ValueError('Use apenas um nome de pasta, sem caminho.')
    if master == projects or master in projects.parents or projects in master.parents:
        raise ValueError('Projetos deve ficar ao lado do mestre, nunca dentro dele.')
    target = safe_path(projects/name)
    if target.exists(): raise ValueError('Destino ja existe; escolha outro nome ou instale no projeto existente.')
    files, folders = [], []
    if source is not None:
        source = safe_path(source)
        if not source.exists(): raise ValueError('Origem nao encontrada.')
        if source == master or source in master.parents or master in source.parents:
            raise ValueError('Nao importe o pacote mestre ou uma pasta que o contenha.')
        if source == projects or source in projects.parents:
            raise ValueError('A origem nao pode conter a pasta Projetos.')
        if source.is_file():
            if excluded(source.name): raise ValueError('Arquivo local excluido da importacao: '+source.name)
            files = [(source, Path(source.name))]
        else:
            def scan_error(error):
                raise error
            for base, directories, names in os.walk(source, followlinks=False, onerror=scan_error):
                directories[:] = [n for n in directories if not excluded(n)]
                for n in directories:
                    directory = safe_path(Path(base)/n)
                    folders.append(directory.relative_to(source))
                for n in names:
                    if not excluded(n):
                        path = safe_path(Path(base)/n)
                        files.append((path, path.relative_to(source)))
    projects.mkdir(parents=True, exist_ok=True)
    stage = safe_path(projects/('.mc-import-'+uuid.uuid4().hex))
    stage.mkdir()
    try:
        for relative in folders:
            (stage/relative).mkdir(parents=True, exist_ok=True)
        for original, relative in files:
            safe_path(original)
            destination = stage/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        install(master, stage, mode=mode, preset=preset)
        validate_project(stage)
        if target.exists(): raise ValueError('Outro processo criou o destino; nada foi sobrescrito.')
        # Windows rename refuses an existing destination, preserving concurrent creations.
        stage.rename(target)
        return target
    finally:
        if stage.exists():
            checked = safe_path(stage)
            if checked.parent != projects or not checked.name.startswith('.mc-import-'):
                raise ValueError('Limpeza fora da pasta temporaria recusada.')
            shutil.rmtree(checked)


def ask_path(prompt):
    value = input(prompt).strip().strip('"')
    if not value: raise ValueError('Informe um caminho.')
    return Path(value)


def choice(prompt, options, default=None):
    value = input(prompt).strip()
    if not value and default is not None: return default
    if value not in options: raise ValueError('Opcao invalida.')
    return options[value]


def main():
    master = Path(__file__).resolve().parents[1]
    projects = master.parent/'Projetos'
    print('\nCodex-Multicontroller - Assistente de projetos')
    print('Mestre:', master)
    print('Pasta padrao de projetos:', projects)
    print('Nao instala programas, nao envia ao GitHub e nao modifica configuracao global.\n')
    while True:
        print('1 - Criar projeto vazio\n2 - Importar pasta ou arquivo para NOVO projeto\n3 - Instalar em projeto existente\n4 - Validar configuracao de projeto\n0 - Sair')
        try:
            action = choice('Escolha: ', {str(i):i for i in range(5)})
            if action == 0: return 0
            if action == 4:
                result = validate_project(ask_path('Pasta do projeto: '))
                print(json.dumps(result, indent=2, ensure_ascii=False))
                print('Apenas configuracao verificada; testes do produto e ativacao remota sao separados.\n')
                continue
            source = ask_path('Pasta ou arquivo de origem (copiar, sem mover): ') if action == 2 else None
            if action in (1,2):
                name = input('Nome do novo projeto: ').strip()
                normalized_path(name)
                if '/' in name: raise ValueError('Informe um nome, sem caminho.')
                target = projects/name
            else:
                target = ask_path('Pasta do projeto existente: ')
            mode = choice('Modo: 1 SOLO [padrao], 2 CO-OP: ', {'1':'SOLO','2':'CO-OP'}, 'SOLO')
            preset = choice('Preset: 1 balanced [padrao], 2 critical: ', {'1':'balanced','2':'critical'}, 'balanced')
            print('\nDestino:', target)
            print('Modo:', mode, '| Preset:', preset)
            if source:
                print('Origem preservada:', source)
                print('Nao copia .git, ambientes virtuais, node_modules, __pycache__ ou .env pessoais.')
                print('Modelos .env.example/.sample/.template sao mantidos. Outras credenciais nao sao detectadas automaticamente.')
                print('Configuracoes AGENTS/.codex existentes podem exigir conciliacao manual.')
            if action == 3:
                print(json.dumps(install(master,target,mode,preset,preview=True),indent=2,ensure_ascii=False))
            if input('Confirmar? Digite S para continuar: ').strip().casefold() != 's':
                print('Cancelado; nenhum projeto criado.\n')
                continue
            if action in (1,2): target = create_project(master,projects,name,source,mode,preset)
            else: install(master,target,mode,preset)
            print('\nConcluido:', target)
            print('Abra essa pasta individualmente no Codex. Configure os testes reais do produto.')
            if mode == 'CO-OP': print('Configure identidades e protecoes GitHub conforme docs/COOP-BOOTSTRAP.md.')
            print('O assistente nao cria commits, remotos nem uploads.\n')
        except (EOFError, KeyboardInterrupt):
            print('\nEncerrado.')
            return 0
        except (OSError, ValueError, KeyError) as exc:
            print('\nNao foi possivel concluir:', exc, '\n')


if __name__ == '__main__': sys.exit(main())
