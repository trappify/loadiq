from click.testing import CliRunner

from loadiq.cli.app import cli


def test_cli_help_works(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", "config/example.yaml", "--help"])
    assert result.exit_code == 0
    assert "LoadIQ control utility" in result.output
