import { AGENT_COMPLETION_TOOL } from '../agent/agent-completion.js'
import {
  createFieldsSchemaForType,
  editFieldsSchemaForType,
  listCreatableNodeTypes,
  listWritableNodeTypes
} from '../canvas/node-catalog.js'
import type { RuntimeToolSchema } from '../contracts.js'
import { MAX_SEMANTIC_EDGE_RELATION_LENGTH } from '@conexus/runtime-protocol'

export const NODE_AGENT_TOOL_NAMES = [
  'find',
  'observe',
  'create',
  'edit',
  'use',
  'request_user_input',
  'complete'
] as const

export type NodeAgentToolName = (typeof NODE_AGENT_TOOL_NAMES)[number]
export type NodeAgentToolRuntime = 'agent' | 'canvas' | 'user'

export interface NodeAgentToolMetadata {
  summary: string
  capability: string
  runtime: NodeAgentToolRuntime
}

export const NODE_AGENT_TOOL_METADATA: Readonly<Record<NodeAgentToolName, NodeAgentToolMetadata>> = {
  find: {
    summary: 'Discover canvas and runtime-managed nodes through a lightweight searchable index.',
    capability: 'node-discovery',
    runtime: 'canvas'
  },
  observe: {
    summary: 'Read known nodes, their values, relations, and optional capability contracts.',
    capability: 'node-observation',
    runtime: 'canvas'
  },
  create: {
    summary: 'Atomically create typed canvas nodes and optional relations.',
    capability: 'node-creation',
    runtime: 'canvas'
  },
  edit: {
    summary: 'Atomically patch, move, connect, disconnect, or delete canvas nodes.',
    capability: 'node-editing',
    runtime: 'canvas'
  },
  use: {
    summary: 'Invoke one declared capability on a canvas or runtime-managed node.',
    capability: 'node-use',
    runtime: 'canvas'
  },
  request_user_input: {
    summary: 'Suspend the Agent and request text, choice, or structured input from the user.',
    capability: 'user-interaction',
    runtime: 'user'
  },
  complete: {
    summary: 'Mark the current task done or blocked and provide a final summary.',
    capability: 'completion',
    runtime: 'agent'
  }
}

function nodeCreateAlternatives(): Record<string, unknown>[] {
  return listCreatableNodeTypes().map((type) => {
    const fieldsSchema = createFieldsSchemaForType(type)
    return {
      type: 'object',
      properties: {
        client_ref: { type: 'string', minLength: 1, description: 'Optional unique reference local to this create call. Relations may use it instead of a node id or exact unique label.' },
        type: { const: type },
        label: { type: 'string', minLength: 1 },
        description: { type: 'string', minLength: 1 },
        ...(fieldsSchema?.properties ?? {}),
        harness_node_id: { type: 'string', minLength: 1 }
      },
      required: ['type', 'label', 'description'],
      additionalProperties: false
    }
  })
}

function nodeEditProperties(): Record<string, unknown> {
  const fields = new Map<string, Map<string, unknown>>()
  for (const type of listWritableNodeTypes()) {
    for (const [name, schema] of Object.entries(editFieldsSchemaForType(type)?.properties ?? {})) {
      const alternatives = fields.get(name) ?? new Map<string, unknown>()
      alternatives.set(JSON.stringify(schema), schema)
      fields.set(name, alternatives)
    }
  }
  return Object.fromEntries([...fields].map(([name, alternatives]) => {
    const schemas = [...alternatives.values()]
    return [name, schemas.length === 1 ? schemas[0] : { anyOf: schemas }]
  }))
}

const FIND_NODES_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'find',
    description: 'Discover nodes without reading their values. Returns a lightweight index with ids, types, labels, short descriptions, status, capability ids, and only relations among the returned matches. Runtime-managed nodes such as Web Search and the Harness Library are included when they match. Use observe after choosing ids.',
    parameters: {
      type: 'object',
      properties: {
        scope: { type: 'string', enum: ['current', 'root', 'harness'], description: 'Defaults to current.' },
        harness_node_id: { type: 'string', minLength: 1, description: 'Required when scope=harness.' },
        query: { type: 'string', minLength: 1, maxLength: 2_000 },
        types: { type: 'array', maxItems: 20, uniqueItems: true, items: { type: 'string', minLength: 1 } },
        capabilities: { type: 'array', maxItems: 20, uniqueItems: true, items: { type: 'string', minLength: 1 } },
        relation_to: { type: 'string', minLength: 1, description: 'Return nodes related to this node id.' },
        statuses: { type: 'array', maxItems: 20, uniqueItems: true, items: { type: 'string', minLength: 1 } },
        cursor: { type: 'string', minLength: 1 },
        limit: { type: 'integer', minimum: 1, maximum: 100 }
      },
      required: [],
      additionalProperties: false
    }
  }
}

const OBSERVE_NODES_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'observe',
    description: 'Read one or more known node ids. Returns safe values, relations, and compact capability descriptors. Set include_contracts=true only when exact capability input/output or edit schemas are needed. Runtime-managed node ids are observable. String fields may be restricted to an inclusive 1-based line range.',
    parameters: {
      type: 'object',
      properties: {
        requests: {
          type: 'array',
          minItems: 1,
          maxItems: 20,
          items: {
            type: 'object',
            properties: {
              node_id: { type: 'string', minLength: 1 },
              detail: { type: 'string', enum: ['values', 'full'], description: 'Defaults to values.' },
              fields: { type: 'array', maxItems: 64, uniqueItems: true, items: { type: 'string', minLength: 1 } },
              include_contracts: { type: 'boolean', description: 'Include full capability input/output and edit schemas. Defaults to false.' },
              start_line: { type: 'integer', minimum: 1 },
              end_line: { type: 'integer', minimum: 1 }
            },
            required: ['node_id'],
            additionalProperties: false
          }
        }
      },
      required: ['requests'],
      additionalProperties: false
    }
  }
}

const CREATE_NODES_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'create',
    description: 'Atomically create typed canvas nodes and optional semantic relations. Type-specific fields are placed directly on each node item, and the complete contract for every node type is embedded in nodes.items. Relations accept a client_ref, node id, or exact unique Canvas label; ambiguous labels fail the entire call.',
    parameters: {
      type: 'object',
      properties: {
        nodes: {
          type: 'array',
          minItems: 1,
          maxItems: 50,
          items: { oneOf: nodeCreateAlternatives() }
        },
        relations: {
          type: 'array',
          maxItems: 100,
          items: {
            type: 'object',
            properties: {
              node_a_ref: { type: 'string', minLength: 1, description: 'First node client_ref, node id, or exact unique label anywhere in the Canvas.' },
              node_b_ref: { type: 'string', minLength: 1, description: 'Second node client_ref, node id, or exact unique label anywhere in the Canvas.' },
              relation: { type: 'string', minLength: 1, maxLength: MAX_SEMANTIC_EDGE_RELATION_LENGTH }
            },
            required: ['node_a_ref', 'node_b_ref', 'relation'],
            additionalProperties: false
          }
        }
      },
      required: ['nodes'],
      additionalProperties: false
    }
  }
}

const EDIT_NODES_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'edit',
    description: 'Atomically apply an ordered batch of node and relation edits. Each operation is tagged by kind. A patch puts every changed node field, including label and description, in set; fields are validated against the target node contract.',
    parameters: {
      type: 'object',
      properties: {
        operations: {
          type: 'array',
          minItems: 1,
          maxItems: 100,
          items: {
            oneOf: [
              {
                type: 'object',
                properties: {
                  kind: { const: 'patch' },
                  node_id: { type: 'string', minLength: 1 },
                  set: {
                    type: 'object',
                    // Providers that close object schemas must still see the
                    // fields a non-empty patch can contain. The target node's
                    // contract remains authoritative at execution time.
                    properties: nodeEditProperties(),
                    minProperties: 1,
                    additionalProperties: false,
                    description: 'Changed node fields. Use the edit_schema returned by observe(include_contracts=true).'
                  }
                },
                required: ['kind', 'node_id', 'set'],
                additionalProperties: false
              },
              {
                type: 'object',
                properties: {
                  kind: { const: 'move' },
                  node_id: { type: 'string', minLength: 1 },
                  scope: { type: 'string', enum: ['root', 'harness'] },
                  harness_node_id: { type: 'string', minLength: 1 }
                },
                required: ['kind', 'node_id', 'scope'],
                additionalProperties: false
              },
              {
                type: 'object',
                properties: {
                  kind: { const: 'connect' },
                  node_a_id: { type: 'string', minLength: 1 },
                  node_b_id: { type: 'string', minLength: 1 },
                  relation: { type: 'string', minLength: 1, maxLength: MAX_SEMANTIC_EDGE_RELATION_LENGTH }
                },
                required: ['kind', 'node_a_id', 'node_b_id', 'relation'],
                additionalProperties: false
              },
              {
                type: 'object',
                properties: {
                  kind: { const: 'disconnect' },
                  edge_id: { type: 'string', minLength: 1 }
                },
                required: ['kind', 'edge_id'],
                additionalProperties: false
              },
              {
                type: 'object',
                properties: {
                  kind: { const: 'delete' },
                  node_id: { type: 'string', minLength: 1 }
                },
                required: ['kind', 'node_id'],
                additionalProperties: false
              }
            ]
          }
        }
      },
      required: ['operations'],
      additionalProperties: false
    }
  }
}

const USE_NODE_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'use',
    description: 'Invoke one capability declared by a canvas or runtime-managed node. Pass the exact node_id and capability id returned by find or observe. input is validated against the capability contract; unfamiliar contracts should be observed before use.',
    parameters: {
      type: 'object',
      properties: {
        node_id: { type: 'string', minLength: 1 },
        capability: { type: 'string', minLength: 1 },
        input: { type: 'object', additionalProperties: true }
      },
      required: ['node_id', 'capability', 'input'],
      additionalProperties: false
    }
  }
}

const REQUEST_USER_INPUT_TOOL: RuntimeToolSchema = {
  type: 'function',
  function: {
    name: 'request_user_input',
    description: 'Suspend execution and ask the user for text, a choice, or structured data. response_schema is JSON Schema for an ephemeral host-rendered form; it does not create a persistent canvas node.',
    parameters: {
      type: 'object',
      properties: {
        prompt: { type: 'string', minLength: 1, maxLength: 4_000 },
        choices: {
          type: 'array',
          minItems: 1,
          maxItems: 12,
          items: { type: 'string', minLength: 1 }
        },
        response_schema: {
          type: 'object',
          description: 'Optional JSON Schema object for structured input.',
          additionalProperties: true
        },
        ui_schema: {
          type: 'object',
          description: 'Optional non-semantic rendering hints keyed by response field.',
          additionalProperties: true
        }
      },
      required: ['prompt'],
      additionalProperties: false
    }
  }
}

export const NODE_AGENT_TOOL_SCHEMAS: Readonly<Record<NodeAgentToolName, RuntimeToolSchema>> = {
  find: FIND_NODES_TOOL,
  observe: OBSERVE_NODES_TOOL,
  create: CREATE_NODES_TOOL,
  edit: EDIT_NODES_TOOL,
  use: USE_NODE_TOOL,
  request_user_input: REQUEST_USER_INPUT_TOOL,
  complete: AGENT_COMPLETION_TOOL
}

export function getNodeAgentToolSchema(name: string): RuntimeToolSchema | undefined {
  return NODE_AGENT_TOOL_NAMES.includes(name as NodeAgentToolName)
    ? NODE_AGENT_TOOL_SCHEMAS[name as NodeAgentToolName]
    : undefined
}

export function resolveNodeAgentToolSchemas(names: readonly string[]): RuntimeToolSchema[] {
  return names.flatMap((name) => {
    const schema = getNodeAgentToolSchema(name)
    return schema ? [schema] : []
  })
}
