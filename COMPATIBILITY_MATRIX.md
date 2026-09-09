# FRcaixinha 1.0 — Compatibility Matrix

## Objetivo

Registrar versões aprovadas, versões em validação e limitações específicas do ambiente Termux/Android.

## Baseline do projeto

| Componente | Versão alvo | Estado |
|---|---:|---|
| Flutter | 3.47.2 Stable | Alvo do projeto |
| Dart | Bundled com Flutter | Alvo |
| Android Studio | Quail 4 2026.1.4 | Alvo |
| AGP | 9.2.0 | Alvo |
| Gradle | 9.4.1 | Alvo |
| JDK | 17 | Alvo |
| Android API | 37 | Alvo |
| Build Tools | 36.0.0 | Alvo |
| Python | 3.13.15 | Alvo |
| PostgreSQL | 18.x | Alvo |
| SQLAlchemy | 2.0.52 | Alvo |
| Alembic | 1.19.2 | Alvo |
| psycopg | 3.3.5 | Alvo |
| Mercado Pago SDK | 3.5.0 | Alvo |

## Ambiente Termux atualmente validado

| Componente | Versão | Estado |
|---|---:|---|
| Flutter | 3.44.9 Stable | Validado localmente |
| Dart | 3.12.2 | Validado |
| Android SDK | API 36 | Validado |
| Build Tools | 34.0.0 adaptado Termux | Validado |
| NDK | 29.0.14206865 | Validado |
| CMake | 4.4.3 | Validado |
| Ninja | 1.13.2 | Validado |
| JDK | 17.0.20 | Validado |
| Gradle | Wrapper do projeto | Validado |
| Android ABI | arm64-v8a | Validado |

## Limitação importante

Flutter 3.47.2 continua sendo o baseline alvo do projeto.

O ambiente Termux atualmente validado utiliza Flutter 3.44.9 porque esta é a versão Termux/ARM64 atualmente validada para construção nativa neste ambiente.

Não alterar o baseline do projeto silenciosamente.

A validação de Flutter 3.47.2 deverá ocorrer posteriormente em ambiente compatível quando necessário.

## Teste de APK já realizado

APK de teste Flutter:

- compilação: OK
- ABI: arm64-v8a
- compileSdk: 34
- targetSdk: 34
- minSdk: 24
- assinatura V2: OK
- instalação no Android: OK
- execução no aparelho: OK

## Regras específicas do Termux

Para APK no Termux:

- `android.aapt2FromMavenOverride` deve apontar para o AAPT2 do Termux;
- `android.enableResourceOptimizations=false`;
- compileSdk/targetSdk compatíveis com a versão de AAPT2 validada;
- somente `arm64-v8a` no build local;
- usar JDK 17;
- usar Gradle Wrapper, nunca depender de Gradle global;
- não usar diretamente binários SDK x86_64 no Android/Termux.

## Critério

Nenhuma combinação de versões entra como oficialmente aprovada para o FRcaixinha sem:

1. compilação;
2. testes;
3. instalação;
4. execução;
5. validação de ABI;
6. validação de assinatura;
7. registro nesta matriz.
