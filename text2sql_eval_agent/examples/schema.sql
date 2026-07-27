-- Sample schema for the demo: a tiny "company" database.
-- Loaded into its own Postgres schema (default: "company") so it can sit
-- alongside other benchmark databases in the same Postgres instance.

CREATE SCHEMA IF NOT EXISTS company;

CREATE TABLE company.departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE company.employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES company.departments(id),
    salary INTEGER NOT NULL,
    hire_year INTEGER NOT NULL
);

INSERT INTO company.departments (id, name) VALUES
    (1, 'Engineering'),
    (2, 'Sales'),
    (3, 'Marketing');

INSERT INTO company.employees (id, name, department_id, salary, hire_year) VALUES
    (1, 'Alice',   1, 7000, 2019),
    (2, 'Bob',     1, 6000, 2020),
    (3, 'Carol',   1, 4999, 2021),
    (4, 'Dave',    2, 5001, 2018),
    (5, 'Eve',     2, 4998, 2022),
    (6, 'Frank',   2, 3000, 2023),
    (7, 'Grace',   3, 5500, 2017),
    (8, 'Heidi',   3, 4500, 2021),
    (9, 'Ivan',    3, 4997, 2020),
    (10, 'Judy',   1, 8000, 2016);
