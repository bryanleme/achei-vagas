"""A vaga, e o que dá pra saber dela sem consultar nada.

`Vaga` é um dado imutável vindo da fonte, mais um punhado de propriedades
derivadas. Ela não decide se passa no filtro (isso é `filtro.py`) nem
quanto vale (isso é `relevancia.py`) — separação de propósito: filtro e
score mudam com o perfil do usuário, a vaga em si não.
"""

import hashlib
import re
from dataclasses import dataclass

from radar import datas
from radar.localizacao import classificar_modalidade, e_remoto, extrair_escopo
from radar.texto import normalizar

# Ordem importa: do mais específico pro mais genérico. "Coordenador" tem de
# vencer antes de qualquer casamento genérico, senão "Coordenador de
# Logística Júnior" classificaria como júnior.
_NIVEIS = [
    ("Liderança", (r"coordenador\w*", r"gerente", r"manager", r"head", r"supervisor\w*", r"diretor\w*")),
    ("Especialista", (r"especialista", r"specialist")),
    ("Sênior", (r"senior", r"sr\.?", r"iii")),
    ("Pleno", (r"pleno", r"pl\.?", r"ii")),
    ("Júnior", (r"junior", r"jr\.?", r"\bi\b")),
    ("Estágio", (r"estagi\w*", r"trainee", r"aprendiz")),
]

NIVEL_NAO_ESPECIFICADO = "Não especificado"


def detectar_senioridade(titulo: str) -> str:
    """Classifica o nível pelo título.

    Nunca exclui vaga — é só informação, usada pra ordenar. Título é um
    indicador fraco de verdade: "PL" no nome nem sempre reflete o que a
    empresa pede na descrição, e vaga sem nível no título é a maioria.
    Classificar sem excluir é a única leitura honesta disso.
    """
    t = normalizar(titulo)
    for nivel, padroes in _NIVEIS:
        for padrao in padroes:
            if re.search(rf"(?<!\w){padrao}(?!\w)", t):
                return nivel
    return NIVEL_NAO_ESPECIFICADO


@dataclass(frozen=True)
class Vaga:
    titulo: str
    empresa: str
    local: str
    link: str
    fonte: str
    modalidade: str = ""
    publicado_em: str = ""  # ISO (AAAA-MM-DD) ou ""
    descricao: str = ""
    termo_busca: str = ""
    # Link da empresa NA FONTE que trouxe esta vaga. É o que permite voltar
    # depois e varrer tudo que ela abriu, sem depender do termo de busca —
    # ver fontes/por_empresa.py. Fica vazio quando a fonte não expõe um.
    empresa_url: str = ""

    @property
    def id(self) -> str:
        """Identidade primária: o link.

        Hash e não o link cru porque o link é longo, varia em parâmetro de
        rastreamento e vira chave de banco. Normalizado antes pra que
        diferença de caixa ou barra final não crie duas identidades pra
        mesma vaga.
        """
        base = normalizar(self.link).rstrip("/")
        return hashlib.md5(base.encode("utf-8")).hexdigest()

    @property
    def chave_secundaria(self) -> str:
        """Identidade secundária: empresa + título.

        Existe porque a MESMA vaga aparece em fontes diferentes com links
        diferentes — a empresa publica na Gupy e no LinkedIn, e o agregador
        republica. Sem esta chave, a mesma vaga notifica duas ou três vezes.

        DEVOLVE "" QUANDO NÃO HÁ EMPRESA, e isso é a parte importante.

        MEDIDO na primeira execução com a InfoJobs ligada: vaga confidencial
        vem sem empresa nenhuma no card, e é comum. Com empresa vazia a
        chave viraria "|analista de pcp" — idêntica para DUAS vagas
        diferentes, de duas empresas diferentes, que por acaso têm o mesmo
        título. A segunda seria descartada como duplicata e nunca notificada.

        Chave vazia desliga a segunda checagem e deixa só o link decidir.
        O erro que sobra é notificar a mesma vaga duas vezes; o erro evitado
        é engolir uma vaga real em silêncio. Duplicar é aceitável, perder
        não.

        ATENÇÃO ao mexer: esta chave é gravada no banco em toda inserção. Se
        a fórmula mudar, o histórico inteiro passa a ter chave incompatível
        e a deduplicação para de valer pro que já está gravado — precisa de
        backfill junto, na mesma migração.
        """
        empresa = normalizar(self.empresa)
        titulo = normalizar(self.titulo)
        if not empresa or not titulo:
            return ""
        return f"{empresa}|{titulo}"

    @property
    def senioridade(self) -> str:
        return detectar_senioridade(self.titulo)

    @property
    def remota(self) -> bool:
        return e_remoto(self.modalidade, self.local)

    @property
    def modalidade_legivel(self) -> str:
        """Modalidade canônica, deduzida do texto de local quando a fonte
        não declara nada."""
        declarada = classificar_modalidade(self.modalidade)
        if declarada:
            return declarada
        return "Remoto" if self.remota else ""

    @property
    def escopo(self) -> set[str]:
        """Mercados que o texto declara — só faz sentido em vaga remota.

        Vaga presencial já está amarrada a uma cidade; o escopo existe pra
        responder "remota PARA QUEM", pergunta que só vaga remota levanta.
        """
        if not self.remota:
            return set()
        return extrair_escopo(self.local)

    def antiga(self, dias_limite: int) -> bool:
        return datas.vaga_antiga(self.publicado_em, dias_limite)

    @property
    def publicado_em_legivel(self) -> str:
        return datas.formatar(self.publicado_em)
